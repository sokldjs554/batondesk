"""평가 명령줄.

    python -m batondesk.evals retrieval --split test
    python -m batondesk.evals agent --split dev --reps 3 --label v1-haiku
    python -m batondesk.evals grounding --split test --reps 3
    python -m batondesk.evals resolution --split test --reps 3
    python -m batondesk.evals baselines --split test
    python -m batondesk.evals compare <실행 A> <실행 B>
    python -m batondesk.evals freeze | check-frozen | index

프롬프트 버전·모델은 서비스와 같은 환경 변수(BATONDESK_AGENT_PROMPT, BATONDESK_AGENT_MODEL …)로 정한다.
`--engine live` 는 ANTHROPIC_API_KEY 가 있어야 하며 실제 비용이 든다.
`--engine rule` 은 LLM이 아닌 규칙 엔진으로 배선만 확인한다. 그 결과는 품질 수치가 아니다.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from batondesk.evals import agent_runner, component, report
from batondesk.evals.data import EVAL_NOW, check_frozen, file_sha256, freeze, load_agent_cases, read_jsonl, suite_path
from batondesk.kb.retriever import KnowledgeBase
from batondesk.llm.base import LLMClient
from batondesk.prompts.registry import PromptRegistry
from batondesk.settings import REPO_ROOT, Settings

DATA = REPO_ROOT / "data" / "evals"
RUNS = REPO_ROOT / "eval_results" / "runs"


def _llm(engine: str, settings: Settings) -> LLMClient:
    if engine == "live":
        if not settings.anthropic_api_key_present:
            sys.exit("ANTHROPIC_API_KEY 가 없습니다. 실제 모델 평가는 키가 있는 환경(GitHub Actions)에서 실행합니다.")
        from batondesk.llm.anthropic_llm import AnthropicLLM

        return AnthropicLLM(timeout_s=settings.llm_timeout_s, max_attempts=settings.llm_max_attempts)
    from batondesk.llm.scripted import RuleLLM

    return RuleLLM()


def _manifest(
    suite: str, split: str, label: str, engine: str, settings: Settings, prompts: PromptRegistry, reps: int, concurrency: int
) -> dict[str, Any]:
    dataset = suite_path(DATA, suite, split)

    def prompt(kind: str, override: str | None) -> dict[str, str]:
        version = prompts.get(kind, override)  # type: ignore[arg-type]
        return {"id": version.id, "sha256": version.sha256}

    run_url = None
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    return {
        "suite": suite,
        "split": split,
        "label": label,
        "engine": "live" if engine == "live" else "rule-engine",
        "quality_numbers": engine == "live",
        "started_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
        "git_sha": settings.resolved_git_sha,
        "github_run_url": run_url,
        "environment": os.environ.get("BATONDESK_EVAL_ENV", "github-actions ubuntu" if run_url else "local"),
        "dataset": {"path": str(dataset.relative_to(REPO_ROOT)), "sha256": file_sha256(dataset), "items": len(read_jsonl(dataset))},
        "config": {
            "agent_model": settings.agent_model,
            "judge_model": settings.judge_model,
            "agent_effort": settings.agent_effort,
            "judge_effort": settings.judge_effort,
            "agent_prompt": prompt("agent", settings.agent_prompt),
            "grounding_prompt": prompt("grounding", settings.grounding_prompt),
            "resolution_prompt": prompt("resolution", settings.resolution_prompt),
            "grounding_mode": settings.grounding_mode,
            "max_revisions": settings.max_revisions,
            "retrieval_k": settings.retrieval_k,
            "tool_timeout_s": settings.tool_timeout_s,
            "tool_retries": settings.tool_retries,
        },
        "reps": reps,
        "concurrency": concurrency,
        "clock": EVAL_NOW.isoformat(),
        "runtime": {
            "python": platform.python_version(),
            "anthropic": importlib.metadata.version("anthropic"),
            "langgraph": importlib.metadata.version("langgraph"),
        },
    }


def _out_dir(args: argparse.Namespace, suite: str) -> Path:
    if args.out:
        return Path(args.out)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return RUNS / f"{stamp}-{suite}-{args.split}-{args.label}"


def _finish(out: Path, manifest: dict[str, Any], summary: dict[str, Any]) -> None:
    manifest["finished_at"] = datetime.now(tz=UTC).isoformat(timespec="seconds")
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n결과 폴더: {out}")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as handle:
            handle.write(report.markdown_for_run(manifest, summary) + "\n")


def cmd_agent(args: argparse.Namespace) -> int:
    settings = Settings()
    prompts = PromptRegistry(settings.prompts_dir)
    cases = load_agent_cases(DATA, args.split)
    if args.case:
        cases = [c for c in cases if c.id in args.case]
    if args.limit:
        cases = cases[: args.limit]
    manifest = _manifest("agent", args.split, args.label, args.engine, settings, prompts, args.reps, args.concurrency)
    manifest["cases_run"] = len(cases)
    out = _out_dir(args, "agent")
    print(
        f"에이전트 평가: {args.split} {len(cases)}문항 × {args.reps}회 · {manifest['config']['agent_prompt']['id']} · {settings.agent_model} · 엔진 {manifest['engine']}"
    )
    result = asyncio.run(
        agent_runner.run_agent_eval(
            cases, settings, llm=_llm(args.engine, settings), reps=args.reps, concurrency=args.concurrency, out_dir=out
        )
    )
    summary = result["summary"]
    print(report.text_for_agent(summary))
    _finish(out, manifest, summary)
    return 0


def cmd_grounding(args: argparse.Namespace) -> int:
    settings = Settings()
    prompts = PromptRegistry(settings.prompts_dir)
    manifest = _manifest("grounding", args.split, args.label, args.engine, settings, prompts, args.reps, args.concurrency)
    llm = None if args.rules_only else _llm(args.engine, settings)
    checker, _ = component.make_checkers(llm, settings, prompts)
    out = _out_dir(args, "grounding")
    result = asyncio.run(
        component.eval_grounding(
            checker, DATA, args.split, reps=args.reps, concurrency=args.concurrency, use_llm=not args.rules_only, out_dir=out
        )
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    _finish(out, manifest, result["summary"])
    return 0


def cmd_resolution(args: argparse.Namespace) -> int:
    settings = Settings()
    prompts = PromptRegistry(settings.prompts_dir)
    manifest = _manifest("resolution", args.split, args.label, args.engine, settings, prompts, args.reps, args.concurrency)
    _, checker = component.make_checkers(_llm(args.engine, settings), settings, prompts)
    out = _out_dir(args, "resolution")
    result = asyncio.run(component.eval_resolution(checker, DATA, args.split, reps=args.reps, concurrency=args.concurrency, out_dir=out))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    _finish(out, manifest, result["summary"])
    return 0


def cmd_retrieval(args: argparse.Namespace) -> int:
    settings = Settings()
    kb = KnowledgeBase.from_dir(settings.kb_dir)
    summary = component.eval_retrieval(kb, DATA, args.split)
    print(json.dumps({k: v for k, v in summary.items() if k != "misses"}, ensure_ascii=False, indent=2))
    print(f"\nhybrid 상위 5위 밖 문항 {len(summary['misses'])}건:")
    for miss in summary["misses"]:
        print(f"  {miss['id']} [{miss['kind']}] {miss['query']} → 정답 {miss['gold']}")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.gate:
        gate = json.loads(Path(args.gate).read_text(encoding="utf-8"))["retrieval"][args.split]
        failed = [
            f"{metric}: {summary['modes']['hybrid'][metric]} < {minimum}"
            for metric, minimum in gate.items()
            if summary["modes"]["hybrid"][metric] < minimum
        ]
        if failed:
            print("\n검색 품질 게이트 실패:\n  " + "\n  ".join(failed), file=sys.stderr)
            return 1
        print("\n검색 품질 게이트 통과:", gate)
    return 0


def cmd_baselines(args: argparse.Namespace) -> int:
    cases = load_agent_cases(DATA, args.split)
    summary = report.baselines(cases)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ceiling = max(v["pass_rate"] for v in summary["policies"].values())
    if ceiling > args.max_pass:
        print(f"무행동 기준선의 통과율({ceiling})이 {args.max_pass}보다 높습니다. 채점이 너무 느슨합니다.", file=sys.stderr)
        return 1
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    result = report.compare(Path(args.a), Path(args.b))
    print(report.text_for_compare(result))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    problems = report.check_agent_gate(Path(args.run), json.loads(Path(args.gate).read_text(encoding="utf-8")))
    for problem in problems:
        print(f"게이트 실패: {problem}", file=sys.stderr)
    if not problems:
        print("에이전트 평가 게이트 통과")
    return 1 if problems else 0


def cmd_freeze(_: argparse.Namespace) -> int:
    print(json.dumps(freeze(DATA), ensure_ascii=False, indent=2))
    return 0


def cmd_check_frozen(_: argparse.Namespace) -> int:
    problems = check_frozen(DATA)
    for problem in problems:
        print(f"오류: {problem}", file=sys.stderr)
    if not problems:
        print("평가용(test) 자료가 동결 기록과 같습니다.")
    return 1 if problems else 0


def cmd_index(args: argparse.Namespace) -> int:
    index = report.build_index(Path(args.runs))
    Path(args.out).write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{args.out}: 섹션 {len(index['sections'])}개")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m batondesk.evals")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, *, llm: bool = True) -> None:
        p.add_argument("--split", choices=["dev", "test"], required=True)
        p.add_argument("--out", default=None)
        if llm:
            p.add_argument("--reps", type=int, default=1)
            p.add_argument("--concurrency", type=int, default=4)
            p.add_argument("--label", default="run")
            p.add_argument("--engine", choices=["live", "rule"], default="live")

    agent = sub.add_parser("agent", help="에이전트 전체 평가")
    common(agent)
    agent.add_argument("--limit", type=int, default=0)
    agent.add_argument("--case", action="append", default=[])
    agent.set_defaults(func=cmd_agent)

    grounding = sub.add_parser("grounding", help="근거 일치 판정기 평가")
    common(grounding)
    grounding.add_argument("--rules-only", action="store_true")
    grounding.set_defaults(func=cmd_grounding)

    resolution = sub.add_parser("resolution", help="해결 여부 판정기 평가")
    common(resolution)
    resolution.set_defaults(func=cmd_resolution)

    retrieval = sub.add_parser("retrieval", help="검색 평가(모델 호출 없음)")
    common(retrieval, llm=False)
    retrieval.add_argument("--gate", default=None)
    retrieval.set_defaults(func=cmd_retrieval)

    base = sub.add_parser("baselines", help="아무 일도 하지 않는 정책이 채점을 얼마나 통과하는지")
    common(base, llm=False)
    base.add_argument("--max-pass", type=float, default=0.35)
    base.set_defaults(func=cmd_baselines)

    compare = sub.add_parser("compare", help="두 실행을 같은 문항끼리 비교")
    compare.add_argument("a")
    compare.add_argument("b")
    compare.add_argument("--out", default=None)
    compare.set_defaults(func=cmd_compare)

    gate = sub.add_parser("gate", help="에이전트 평가 결과가 기준을 넘는지")
    gate.add_argument("run")
    gate.add_argument("--gate", required=True)
    gate.set_defaults(func=cmd_gate)

    sub.add_parser("freeze", help="평가용 자료의 해시를 기록").set_defaults(func=cmd_freeze)
    sub.add_parser("check-frozen", help="평가용 자료가 바뀌지 않았는지 확인").set_defaults(func=cmd_check_frozen)
    index = sub.add_parser("index", help="운영 화면이 읽는 평가 요약 파일 생성")
    index.add_argument("--runs", default=str(RUNS))
    index.add_argument("--out", default=str(REPO_ROOT / "eval_results" / "index.json"))
    index.set_defaults(func=cmd_index)

    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
