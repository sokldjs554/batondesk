"""평가 결과의 요약·비교·기준선·게이트·화면용 색인."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from batondesk.evals.data import AgentCase
from batondesk.evals.graders import grade_case
from batondesk.evals.stats import paired_diff

CATEGORY_LABEL = {
    "kb_answerable": "문서에 답이 있는 문의",
    "followup_context": "이전 대화가 필요한 후속 질문",
    "kb_unanswerable": "문서에 답이 없는 문의",
    "kb_conflict": "문서끼리 내용이 다른 문의",
    "order_invalid": "잘못된·없는 주문 정보",
    "tool_failure": "도구 호출 실패·시간 초과",
    "handoff_required": "상담원에게 넘겨야 하는 문의",
    "order_task": "주문 조회·취소·반품",
    "ungrounded_injection": "근거와 다른 답변 주입",
}
GRADE_LABEL = {
    "action_ok": "행동(답변/되묻기/인계) 일치",
    "facts_ok": "사실 일치",
    "abstain_ok": "모르는 것을 모른다고 함",
    "tools_ok": "도구 사용 일치",
    "end_state_ok": "주문 상태 일치",
    "injection_blocked": "주입한 틀린 답 차단",
    "handoff_reason_ok": "인계 사유 일치",
    "citation_ok": "정답 문서 인용",
    "retrieval_first_hit": "첫 검색에 정답 문서",
    "retrieval_any_hit": "턴 안에 정답 문서 확보",
    "kb_issue_flagged": "문서 문제를 운영 메모로 남김",
    "grounded_online": "근거 검사 통과(파이프라인 판정)",
}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _ci(block: dict[str, Any]) -> str:
    if block.get("mean") is None:
        return "— (해당 없음)"
    return f"{_pct(block['mean'])} [{_pct(block['lo'])}, {_pct(block['hi'])}] (문항 {block['cases']}, 실행 {block['rows']})"


def text_for_agent(summary: dict[str, Any]) -> str:
    lines = [
        "",
        f"통과율: {_ci(summary['pass'])}   ※ 대괄호는 문항 단위 부트스트랩 95% 구간",
        f"채점 {summary['rows_scored']}행 · 제외 {summary['rows_skipped']}행 · 인프라 오류 {summary['rows_errored']}행 · 재시도 후 채점 {summary['retried_rows']}행",
        "",
        "분류별 통과율:",
    ]
    lines += [f"  {CATEGORY_LABEL.get(k, k):24s} {_ci(v)}" for k, v in summary["by_category"].items()]
    lines += ["", "성질별:"]
    lines += [f"  {GRADE_LABEL.get(k, k):26s} {_ci(v)}" for k, v in summary["grades"].items()]
    h = summary["handoff"]
    lines += [
        "",
        f"인계 판단: 정밀도 {_pct(h['precision'])} · 재현율 {_pct(h['recall'])} · 과잉 인계율 {_pct(h['over_handoff_rate'])} (TP {h['tp']} FP {h['fp']} FN {h['fn']} TN {h['tn']})",
        f"반복마다 결과가 갈린 문항: {', '.join(summary['flaky_cases']) or '없음'}",
        f"모든 반복에서 실패한 문항: {', '.join(summary['always_failing_cases']) or '없음'}",
        f"턴 지연 p50 {summary['turns']['latency_ms']['p50']}ms · p95 {summary['turns']['latency_ms']['p95']}ms · 턴당 모델 호출 {summary['turns']['llm_calls_per_turn']}",
        f"추정 비용 ${summary['usage']['total_cost_usd']} (에이전트 ${summary['usage']['agent'].get('cost_usd', 0)} · 판정 ${summary['usage']['checks'].get('cost_usd', 0)})",
    ]
    return "\n".join(lines)


def markdown_for_run(manifest: dict[str, Any], summary: dict[str, Any]) -> str:
    cfg = manifest["config"]
    head = [
        f"### 평가 실행: {manifest['suite']} / {manifest['split']} / {manifest['label']}",
        "",
        f"- 엔진: `{manifest['engine']}`{'' if manifest['quality_numbers'] else ' — **LLM이 아니므로 품질 수치가 아님**'}",
        f"- 프롬프트 `{cfg['agent_prompt']['id']}` · 에이전트 모델 `{cfg['agent_model']}` · 판정 모델 `{cfg['judge_model']}` · 반복 {manifest['reps']}회",
        f"- 자료 `{manifest['dataset']['path']}` ({manifest['dataset']['items']}문항, sha256 `{manifest['dataset']['sha256'][:12]}`) · 커밋 `{manifest['git_sha'][:10]}`",
        "",
    ]
    if manifest["suite"] == "agent":
        return "\n".join(head) + "```\n" + text_for_agent(summary) + "\n```"
    return "\n".join(head) + "```json\n" + json.dumps(summary, ensure_ascii=False, indent=2)[:6000] + "\n```"


# ---- 기준선 -------------------------------------------------------------------

_BASELINE_MESSAGES = {
    "always_handoff": ("handoff", "상담원에게 연결해 드리겠습니다."),
    "always_abstain": ("answer", "죄송합니다. 해당 내용은 안내 문서에서 확인되지 않습니다."),
    "always_ask": ("ask", "주문번호를 알려 주시겠어요?"),
}


def baselines(cases: list[AgentCase]) -> dict[str, Any]:
    """모델 없이 한 가지 행동만 하는 정책이 채점을 얼마나 통과하는가.

    여기서 높게 나오는 문항이 많으면 채점이 느슨한 것이다. 실제 실행의 통과율은 이 값과 나란히 읽는다.
    근거 주입 문항은 주입 자체가 일어나지 않으므로 제외된다.
    """
    out: dict[str, Any] = {"cases": len(cases), "policies": {}}
    for policy, (action, message) in _BASELINE_MESSAGES.items():
        passed, scored = 0, 0
        by_category: dict[str, list[int]] = {}
        for case in cases:
            # 기준선은 아무 도구도 부르지 않으므로 주문 상태는 처음 그대로다.
            state = {order: "seed" for order in case.expect.end_state}
            run: dict[str, Any] = {
                "ai_messages": [message],
                "final_action": action,
                "handed_off": action == "handoff",
                "handoff_reason": "needs_human_review" if action == "handoff" else None,
                "tools": [],
                "injected": False,
                "first_pass_docs": [],
                "pool_docs": [],
                "cited_docs": [],
                "kb_issue": None,
                "last_grounding": None,
                "seed_state": state,
                "end_state": dict(state),
            }
            graded = grade_case(case, run)
            if graded["passed"] is None:
                continue
            scored += 1
            passed += int(graded["passed"])
            bucket = by_category.setdefault(case.category, [0, 0])
            bucket[0] += int(graded["passed"])
            bucket[1] += 1
        out["policies"][policy] = {
            "pass_rate": round(passed / scored, 4) if scored else None,
            "passed": passed,
            "scored": scored,
            "by_category": {k: f"{v[0]}/{v[1]}" for k, v in by_category.items()},
        }
    return out


# ---- 비교 ---------------------------------------------------------------------


def _load_run(path: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (path / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    return manifest, summary, rows


def _by_case(rows: list[dict[str, Any]], key: str | None = None) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for row in rows:
        value = row["passed"] if key is None else row["grades"].get(key)
        if value is not None:
            out.setdefault(row["case_id"], []).append(float(value))
    return out


def compare(a_dir: Path, b_dir: Path) -> dict[str, Any]:
    """같은 문항끼리 짝지어 b − a 를 본다. 자료가 다르면 비교하지 않는다."""
    ma, sa, ra = _load_run(a_dir)
    mb, sb, rb = _load_run(b_dir)
    if ma["dataset"]["sha256"] != mb["dataset"]["sha256"]:
        raise ValueError("두 실행의 평가 자료가 다릅니다. 같은 자료로 돌린 실행끼리만 비교합니다.")
    pa, pb = _by_case(ra), _by_case(rb)
    changed: list[dict[str, Any]] = []
    for cid in sorted(set(pa) & set(pb)):
        before, after = sum(pa[cid]) / len(pa[cid]), sum(pb[cid]) / len(pb[cid])
        if before != after:
            reasons = sorted({r for row in (rb if after < before else ra) if row["case_id"] == cid for r in row["fail_reasons"]})
            changed.append({"case_id": cid, "before": round(before, 3), "after": round(after, 3), "fail_reasons": reasons})

    def side(manifest: dict[str, Any], summary: dict[str, Any], path: Path) -> dict[str, Any]:
        return {
            "run": path.name,
            "label": manifest["label"],
            "prompt": manifest["config"]["agent_prompt"]["id"],
            "model": manifest["config"]["agent_model"],
            "judge_model": manifest["config"]["judge_model"],
            "reps": manifest["reps"],
            "pass": summary["pass"],
            "latency_p50_ms": summary["turns"]["latency_ms"]["p50"],
            "latency_p95_ms": summary["turns"]["latency_ms"]["p95"],
            "cost_usd": summary["usage"]["total_cost_usd"],
            "handoff": {k: summary["handoff"][k] for k in ("precision", "recall", "over_handoff_rate")},
        }

    return {
        "dataset": ma["dataset"],
        "a": side(ma, sa, a_dir),
        "b": side(mb, sb, b_dir),
        "pass_diff": paired_diff(pa, pb),
        "grade_diffs": {key: paired_diff(_by_case(ra, key), _by_case(rb, key)) for key in sa["grades"]},
        "by_category": {
            category: {"a": sa["by_category"][category]["mean"], "b": sb["by_category"].get(category, {}).get("mean")}
            for category in sa["by_category"]
        },
        "improved": [c for c in changed if float(c["after"]) > float(c["before"])],
        "regressed": [c for c in changed if float(c["after"]) < float(c["before"])],
    }


def text_for_compare(result: dict[str, Any]) -> str:
    a, b, d = result["a"], result["b"], result["pass_diff"]
    lines = [
        f"A: {a['label']} ({a['prompt']}, {a['model']}) 통과율 {_pct(a['pass']['mean'])}",
        f"B: {b['label']} ({b['prompt']}, {b['model']}) 통과율 {_pct(b['pass']['mean'])}",
        f"같은 문항 {d['cases']}개에서 B − A = {d['diff'] * 100:+.1f}%p [95% {d['lo'] * 100:+.1f}, {d['hi'] * 100:+.1f}] · 좋아진 문항 {d['improved']} · 나빠진 문항 {d['regressed']}",
        "",
    ]
    for key, diff in result["grade_diffs"].items():
        if diff["diff"] is not None and diff["diff"] != 0:
            lines.append(
                f"  {GRADE_LABEL.get(key, key):26s} {diff['diff'] * 100:+.1f}%p [{diff['lo'] * 100:+.1f}, {diff['hi'] * 100:+.1f}]"
            )
    for title, items in (("좋아진 문항", result["improved"]), ("나빠진 문항", result["regressed"])):
        lines.append(f"\n{title}:")
        lines += [f"  {c['case_id']}: {c['before']} → {c['after']}  {'; '.join(c['fail_reasons'])[:160]}" for c in items] or ["  없음"]
    return "\n".join(lines)


# ---- 게이트 -------------------------------------------------------------------


def check_agent_gate(run_dir: Path, gate: dict[str, Any]) -> list[str]:
    """프롬프트·에이전트 변경이 넘어야 하는 기준. 기준 값은 eval_results/gate.json 에 둔다."""
    manifest, summary, _ = _load_run(run_dir)
    rules = gate["agent"][manifest["split"]]
    problems = []
    if summary["rows_errored"] > rules.get("max_errored_rows", 0):
        problems.append(f"인프라 오류로 채점하지 못한 실행 {summary['rows_errored']}건 (허용 {rules.get('max_errored_rows', 0)})")
    if summary["pass"]["mean"] is None or summary["pass"]["mean"] < rules["min_pass_rate"]:
        problems.append(f"통과율 {summary['pass']['mean']} < {rules['min_pass_rate']}")
    for key, minimum in rules.get("min_grades", {}).items():
        value = summary["grades"][key]["mean"]
        if value is not None and value < minimum:
            problems.append(f"{GRADE_LABEL.get(key, key)} {value} < {minimum}")
    return problems


# ---- 운영 화면용 색인 ------------------------------------------------------------


def build_index(runs_dir: Path) -> dict[str, Any]:
    """운영 화면의 '프롬프트·평가' 탭이 읽는 파일. 실제 모델로 돌린 실행만 싣는다."""
    runs = []
    for path in sorted(runs_dir.glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        summary_path = path.parent / "summary.json"
        if not manifest.get("quality_numbers") or not summary_path.exists():
            continue
        runs.append((path.parent.name, manifest, json.loads(summary_path.read_text(encoding="utf-8"))))
    if not runs:
        return {"available": False, "note": "실제 모델로 돌린 평가 결과가 아직 저장소에 없습니다.", "sections": []}

    def base(name: str, m: dict[str, Any]) -> dict[str, Any]:
        return {
            "run": m["label"],
            "split": {"dev": "개발용", "test": "평가용"}[m["split"]],
            "date": m["started_at"][:10],
            "commit": m["git_sha"][:8],
            "run_url": m.get("github_run_url") or "",
            "run_id": name,
        }

    agent_rows, grounding_rows, resolution_rows = [], [], []
    for name, m, s in runs:
        cfg = m["config"]
        if m["suite"] == "agent":
            agent_rows.append(
                {
                    **base(name, m),
                    "prompt": cfg["agent_prompt"]["id"],
                    "model": cfg["agent_model"],
                    "n": f"{s['cases']}×{s['reps']}",
                    "pass": s["pass"]["mean"],
                    "pass_ci": [s["pass"]["lo"], s["pass"]["hi"]],
                    "handoff_precision": s["handoff"]["precision"],
                    "handoff_recall": s["handoff"]["recall"],
                    "end_state": s["grades"]["end_state_ok"]["mean"],
                    "injection": s["grades"]["injection_blocked"]["mean"],
                    "p50": s["turns"]["latency_ms"]["p50"],
                    "p95": s["turns"]["latency_ms"]["p95"],
                    "cost": s["usage"]["total_cost_usd"],
                }
            )
        elif m["suite"] == "grounding" and "judge" in s:
            j = s["judge"]
            grounding_rows.append(
                {
                    **base(name, m),
                    "prompt": cfg["grounding_prompt"]["id"],
                    "model": cfg["judge_model"],
                    "n": f"{s['items']}×{s['reps']}",
                    "accuracy": j["accuracy"],
                    "recall": j["recall"],
                    "specificity": j["specificity"],
                    "precision": j["precision"],
                    "rules_recall": s["rules_hard_only"]["recall"],
                    "cost": s["usage"]["cost_usd"],
                }
            )
        elif m["suite"] == "resolution":
            b = s["resolved_vs_unresolved"]
            resolution_rows.append(
                {
                    **base(name, m),
                    "prompt": cfg["resolution_prompt"]["id"],
                    "model": cfg["judge_model"],
                    "n": f"{s['items']}×{s['reps']}",
                    "accuracy": s["accuracy_5class"]["mean"],
                    "unresolved_recall": b["recall"],
                    "resolved_recall": b["specificity"],
                    "cost": s["usage"]["cost_usd"],
                }
            )

    common_tail = [{"key": "date", "label": "실행일", "format": "text"}, {"key": "commit", "label": "커밋", "format": "text"}]
    sections = []
    if agent_rows:
        sections.append(
            {
                "title": "에이전트 전체 평가",
                "description": "같은 문항을 반복 실행한 통과율. 대괄호는 문항 단위 부트스트랩 95% 구간.",
                "columns": [
                    {"key": "run", "label": "실행", "format": "text"},
                    {"key": "split", "label": "자료", "format": "text"},
                    {"key": "prompt", "label": "프롬프트", "format": "text"},
                    {"key": "model", "label": "모델", "format": "text"},
                    {"key": "n", "label": "문항×반복", "format": "text"},
                    {"key": "pass", "label": "통과율", "format": "pct"},
                    {"key": "pass_ci", "label": "95% 구간", "format": "ci"},
                    {"key": "handoff_precision", "label": "인계 정밀도", "format": "pct"},
                    {"key": "handoff_recall", "label": "인계 재현율", "format": "pct"},
                    {"key": "end_state", "label": "주문 상태 일치", "format": "pct"},
                    {"key": "injection", "label": "주입 차단", "format": "pct"},
                    {"key": "p50", "label": "지연 p50", "format": "ms"},
                    {"key": "p95", "label": "지연 p95", "format": "ms"},
                    {"key": "cost", "label": "추정 비용", "format": "usd"},
                    *common_tail,
                ],
                "rows": agent_rows,
                "footnote": "채점은 규칙(행동·사실·도구·주문 상태)으로 한다. 정답 라벨은 AI가 작성했고 사람 검수 범위는 docs/evaluation.md 에 적었다.",
            }
        )
    if grounding_rows:
        sections.append(
            {
                "title": "근거 일치 판정기",
                "description": "라벨된 답변에서 '근거와 어긋남'을 얼마나 잡는지(재현율)와 멀쩡한 답을 얼마나 통과시키는지(특이도).",
                "columns": [
                    {"key": "run", "label": "실행", "format": "text"},
                    {"key": "split", "label": "자료", "format": "text"},
                    {"key": "prompt", "label": "프롬프트", "format": "text"},
                    {"key": "model", "label": "판정 모델", "format": "text"},
                    {"key": "n", "label": "문항×반복", "format": "text"},
                    {"key": "accuracy", "label": "정확도", "format": "pct"},
                    {"key": "recall", "label": "어긋남 재현율", "format": "pct"},
                    {"key": "specificity", "label": "특이도", "format": "pct"},
                    {"key": "precision", "label": "정밀도", "format": "pct"},
                    {"key": "rules_recall", "label": "규칙만의 재현율", "format": "pct"},
                    {"key": "cost", "label": "추정 비용", "format": "usd"},
                    *common_tail,
                ],
                "rows": grounding_rows,
                "footnote": "판정기도 틀린다. 놓친 문항과 잘못 막은 문항의 목록은 실행 폴더의 summary.json 에 있다.",
            }
        )
    if resolution_rows:
        sections.append(
            {
                "title": "해결 여부 판정기",
                "description": "라벨된 대화의 5분류 정확도와, 미해결 대화를 미해결로 맞힌 비율.",
                "columns": [
                    {"key": "run", "label": "실행", "format": "text"},
                    {"key": "split", "label": "자료", "format": "text"},
                    {"key": "prompt", "label": "프롬프트", "format": "text"},
                    {"key": "model", "label": "판정 모델", "format": "text"},
                    {"key": "n", "label": "문항×반복", "format": "text"},
                    {"key": "accuracy", "label": "5분류 정확도", "format": "pct"},
                    {"key": "unresolved_recall", "label": "미해결을 미해결로", "format": "pct"},
                    {"key": "resolved_recall", "label": "해결을 해결로", "format": "pct"},
                    {"key": "cost", "label": "추정 비용", "format": "usd"},
                    *common_tail,
                ],
                "rows": resolution_rows,
                "footnote": "LLM 판정은 '해결됐다'고 쉽게 말하는 경향이 있어 미해결 쪽 수치를 따로 본다.",
            }
        )
    return {"available": True, "note": "저장소에 커밋된 평가 실행 결과입니다. 실행 로그는 GitHub Actions에 있습니다.", "sections": sections}
