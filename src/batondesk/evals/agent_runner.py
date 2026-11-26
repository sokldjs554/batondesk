"""에이전트 평가 실행기.

서비스가 실제로 쓰는 진입점(SupportService.handle_customer_message)을 그대로 부른다.
평가용으로 따로 짠 호출 경로가 없으므로, 평가 설정과 서비스 설정이 어긋날 수 없다.

규칙:
- 문항·반복마다 새 샌드박스와 새 대화. 앞 실행이 남긴 주문 상태가 다음 채점에 섞이지 않는다.
- 모델 API의 일시 장애(한도 초과·서버 오류·연결 실패)는 모델의 실패가 아니다. 그런 시도는
  다시 실행하고, 끝내 실패하면 results 가 아니라 errors 에 남긴다(점수에 0으로 섞지 않는다).
  모델의 거절·잘린 출력·형식 오류는 모델의 결과이므로 그대로 채점한다.
- 한 문항의 벽시계 상한을 둔다. 넘기면 errors 로 간다.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from batondesk.evals.data import EVAL_NOW, AgentCase
from batondesk.evals.graders import grade_case
from batondesk.evals.stats import cluster_ci, group
from batondesk.llm.base import LLMClient
from batondesk.obs.metrics import percentile
from batondesk.service import ServiceError, SupportService
from batondesk.settings import Settings
from batondesk.store.db import Database

CASE_TIMEOUT_S = 240.0
INFRA_ATTEMPTS = 3
GRADE_KEYS = (
    "action_ok",
    "facts_ok",
    "abstain_ok",
    "tools_ok",
    "end_state_ok",
    "injection_blocked",
    "handoff_reason_ok",
    "citation_ok",
    "retrieval_first_hit",
    "retrieval_any_hit",
    "kb_issue_flagged",
    "grounded_online",
)


def _usage_split(turns: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {
        "agent": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0},
        "checks": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0},
    }
    for turn in turns:
        for step in (turn["detail"] or {}).get("steps", []):
            if step["type"] != "llm":
                continue
            bucket = out["agent" if step["purpose"] == "agent" else "checks"]
            bucket["calls"] += 1
            bucket["input_tokens"] += step["usage"]["input_tokens"]
            bucket["output_tokens"] += step["usage"]["output_tokens"]
            bucket["cache_read_tokens"] += step["usage"]["cache_read_input_tokens"]
            bucket["cost_usd"] += step.get("cost_usd") or 0.0
    return out


def collect_run(service: SupportService, conversation_id: str, sandbox_id: str, seed_state: dict[str, str]) -> dict[str, Any]:
    """채점에 필요한 것만 대화 기록에서 뽑는다."""
    view = service.conversation_view(conversation_id, with_trace=True)
    turns = view["turns"]
    ai = [m for m in view["messages"] if m["role"] == "ai"]
    steps = [s for t in turns for s in (t["detail"] or {}).get("steps", [])]
    evidence = view["evidence"] or []
    by_id = {e["id"]: e for e in evidence}
    last_payload = ai[-1]["payload"] if ai else {}
    handoff = view["handoff"]
    snapshot = service.commerce_store.sandbox(sandbox_id).snapshot()
    return {
        "ai_messages": [m["text"] for m in ai],
        "final_action": last_payload.get("action"),
        "handed_off": handoff is not None,
        "handoff_reason": handoff["reason"] if handoff else None,
        "handoff_source": handoff["source"] if handoff else None,
        "tools": [{"name": s["name"], "status": s["status"], "attempts": s.get("attempts", 1)} for s in steps if s["type"] == "tool"],
        "injected": any(s["type"] == "eval_injection" for s in steps),
        "first_pass_docs": sorted({h["chunk_id"].split("#")[0] for s in steps if s["type"] == "retrieval" for h in s["hits"]}),
        "pool_docs": sorted({e["meta"]["doc_id"] for e in evidence if e["kind"] == "kb"}),
        "cited_docs": sorted(
            {by_id[c["id"]]["meta"]["doc_id"] for c in last_payload.get("citations", []) if c["id"] in by_id and c["kind"] == "kb"}
        ),
        "kb_issue": next((t["kb_issue"] for t in turns if t["kb_issue"]), None),
        "last_grounding": turns[-1]["grounding_verdict"] if turns else None,
        "seed_state": seed_state,
        "end_state": {order: info["status"] for order, info in snapshot.items()},
        "transcript": [{"role": m["role"], "text": m["text"]} for m in view["messages"]],
        "turns": [
            {
                "turn_seq": t["turn_seq"],
                "latency_ms": t["latency_ms"],
                "outcome": t["outcome"],
                "model": t["model"],
                "llm_calls": t["llm_calls"],
                "tool_calls": t["tool_calls"],
                "tool_errors": t["tool_errors"],
                "revisions": t["revisions"],
                "blocked_count": t["blocked_count"],
                "grounding_verdict": t["grounding_verdict"],
                "error": t["error"],
                "stage_latency_ms": (t["detail"] or {}).get("stage_latency_ms"),
            }
            for t in turns
        ],
        "usage": _usage_split(turns),
        "errors": [t["error"] for t in turns if t["error"]],
        "trace": turns,
        "evidence": evidence,
    }


async def run_case_once(service: SupportService, case: AgentCase, rep: int) -> dict[str, Any]:
    sandbox_id = f"eval-{case.id}-{rep}-{uuid.uuid4().hex[:8]}"
    sandbox = service.commerce_store.reset(sandbox_id, fixed_now=EVAL_NOW)
    seed_state = {order: info["status"] for order, info in sandbox.snapshot().items()}
    sandbox.fault = case.fault
    conversation = service.create_conversation(case.customer_id, channel="eval", sandbox_id=sandbox_id)
    try:
        for index, text in enumerate(case.turns):
            inject = case.inject_draft if index == len(case.turns) - 1 else None
            await service.handle_customer_message(conversation["id"], text, inject_draft=inject)
        return collect_run(service, conversation["id"], sandbox_id, seed_state)
    finally:
        service.commerce_store.drop(sandbox_id)


def _infra_error(run: dict[str, Any]) -> str | None:
    return next((e for e in run["errors"] if e.startswith("llm_")), None)


async def run_agent_eval(
    cases: list[AgentCase],
    settings: Settings,
    *,
    llm: LLMClient | None = None,
    reps: int = 1,
    concurrency: int = 4,
    out_dir: Path | None = None,
    progress: bool = True,
) -> dict[str, Any]:
    settings.resolution_check = False  # 해결 여부 판정기는 별도 자료로 평가한다
    settings.daily_usd_cap = 1e9
    settings.max_customer_turns = 50
    service = SupportService(settings, llm=llm, db=Database(":memory:"))
    semaphore = asyncio.Semaphore(concurrency)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    trajectories: list[dict[str, Any]] = []
    started = time.time()
    done = 0

    async def one(case: AgentCase, rep: int) -> None:
        nonlocal done
        async with semaphore:
            attempt, failure = 0, "unknown"
            run: dict[str, Any] | None = None
            while attempt < INFRA_ATTEMPTS:
                attempt += 1
                try:
                    run = await asyncio.wait_for(run_case_once(service, case, rep), timeout=CASE_TIMEOUT_S)
                except TimeoutError:
                    run, failure = None, "case_timeout"
                    break
                except ServiceError as exc:
                    run, failure = None, f"service_error:{exc.code}"
                    break
                failure = _infra_error(run) or ""
                if not failure:
                    break
                run = None
                await asyncio.sleep(2.0 * attempt)
            if run is None:
                errors.append({"case_id": case.id, "category": case.category, "rep": rep, "failure": failure, "attempts": attempt})
            else:
                graded = grade_case(case, run)
                rows.append(
                    {
                        "case_id": case.id,
                        "category": case.category,
                        "rep": rep,
                        "attempts": attempt,
                        "passed": graded["passed"],
                        "skipped": graded["skipped"],
                        "grades": graded["grades"],
                        "fail_reasons": graded["fail_reasons"],
                        "expected_handoff": graded["expected_handoff"],
                        "actual_handoff": graded["actual_handoff"],
                        "final_action": run["final_action"],
                        "handoff_reason": run["handoff_reason"],
                        "handoff_source": run["handoff_source"],
                        "tools": run["tools"],
                        "injected": run["injected"],
                        "model_errors": run["errors"],
                        "transcript": run["transcript"],
                        "turns": run["turns"],
                        "usage": run["usage"],
                    }
                )
                trajectories.append({"case_id": case.id, "rep": rep, "trace": run["trace"], "evidence": run["evidence"]})
            done += 1
            if progress and (done % 10 == 0 or done == len(cases) * reps):
                print(f"  {done}/{len(cases) * reps} 실행 완료", flush=True)

    await asyncio.gather(*(one(case, rep) for case in cases for rep in range(1, reps + 1)))
    engine = service.engine
    await service.aclose()
    rows.sort(key=lambda r: (r["case_id"], r["rep"]))
    errors.sort(key=lambda r: (r["case_id"], r["rep"]))
    summary = summarize(rows, errors, cases=len(cases), reps=reps)
    summary["wall_clock_s"] = round(time.time() - started, 1)
    summary["engine"] = engine
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_jsonl(out_dir / "results.jsonl", rows)
        _write_jsonl(out_dir / "errors.jsonl", errors)
        with gzip.open(out_dir / "trajectories.jsonl.gz", "wt", encoding="utf-8") as handle:
            for item in sorted(trajectories, key=lambda r: (r["case_id"], r["rep"])):
                handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
        (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"summary": summary, "rows": rows, "errors": errors}


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def summarize(rows: list[dict[str, Any]], errors: list[dict[str, Any]], *, cases: int, reps: int) -> dict[str, Any]:
    """요약은 results 의 행에서만 다시 계산한다(집계 필드를 따로 믿지 않는다)."""
    scored = [r for r in rows if r["passed"] is not None]
    flat = [{"case_id": r["case_id"], "category": r["category"], "passed": float(r["passed"])} for r in scored]
    by_category: dict[str, Any] = {}
    for category in sorted({r["category"] for r in rows}):
        subset = [f for f in flat if f["category"] == category]
        by_category[category] = cluster_ci(group(subset, "case_id", "passed"))

    grades: dict[str, Any] = {}
    for key in GRADE_KEYS:
        applicable = [{"case_id": r["case_id"], "v": float(r["grades"][key])} for r in rows if r["grades"].get(key) is not None]
        grades[key] = cluster_ci(group(applicable, "case_id", "v"))

    tp = sum(1 for r in rows if r["expected_handoff"] is True and r["actual_handoff"])
    fn = sum(1 for r in rows if r["expected_handoff"] is True and not r["actual_handoff"])
    fp = sum(1 for r in rows if r["expected_handoff"] is False and r["actual_handoff"])
    tn = sum(1 for r in rows if r["expected_handoff"] is False and not r["actual_handoff"])

    per_case: dict[str, list[bool]] = defaultdict(list)
    for r in scored:
        per_case[r["case_id"]].append(bool(r["passed"]))
    flaky = sorted(cid for cid, values in per_case.items() if len(set(values)) > 1)
    always_fail = sorted(cid for cid, values in per_case.items() if not any(values))

    turns = [t for r in rows for t in r["turns"]]
    latencies = [float(t["latency_ms"]) for t in turns]
    stage: dict[str, list[float]] = defaultdict(list)
    for t in turns:
        for name, value in (t["stage_latency_ms"] or {}).items():
            stage[name].append(float(value))
    usage: dict[str, dict[str, float]] = {"agent": defaultdict(float), "checks": defaultdict(float)}
    for r in rows:
        for bucket in ("agent", "checks"):
            for name, value in r["usage"][bucket].items():
                usage[bucket][name] += value
    usage_out = {b: {k: (round(v, 5) if k == "cost_usd" else int(v)) for k, v in usage[b].items()} for b in usage}
    total_cost = round(usage_out["agent"].get("cost_usd", 0) + usage_out["checks"].get("cost_usd", 0), 5)

    overall = cluster_ci(group(flat, "case_id", "passed"))
    return {
        "cases": cases,
        "reps": reps,
        "rows_scored": len(scored),
        "rows_skipped": len(rows) - len(scored),
        "rows_errored": len(errors),
        "pass": overall,
        "by_category": by_category,
        "grades": grades,
        "handoff": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "over_handoff_rate": round(fp / (fp + tn), 4) if fp + tn else None,
            "by_reason": dict(Counter(r["handoff_reason"] for r in rows if r["actual_handoff"])),
            "by_source": dict(Counter(r["handoff_source"] for r in rows if r["actual_handoff"])),
        },
        "flaky_cases": flaky,
        "always_failing_cases": always_fail,
        "model_outcome_errors": dict(Counter(e for r in rows for e in r["model_errors"])),
        "infra_errors": dict(Counter(e["failure"] for e in errors)),
        "retried_rows": sum(1 for r in rows if r["attempts"] > 1),
        "turns": {
            "count": len(turns),
            "latency_ms": {
                "p50": percentile(latencies, 0.5),
                "p95": percentile(latencies, 0.95),
                "max": round(max(latencies), 1) if latencies else None,
            },
            "stage_latency_ms_p50": {k: percentile(v, 0.5) for k, v in sorted(stage.items())},
            "llm_calls_per_turn": round(sum(t["llm_calls"] for t in turns) / len(turns), 2) if turns else None,
            "tool_calls_per_turn": round(sum(t["tool_calls"] for t in turns) / len(turns), 2) if turns else None,
            "turns_with_blocked_draft": sum(1 for t in turns if t["blocked_count"]),
            "models_served": dict(Counter(t["model"] for t in turns)),
        },
        "usage": {**usage_out, "total_cost_usd": total_cost},
    }
