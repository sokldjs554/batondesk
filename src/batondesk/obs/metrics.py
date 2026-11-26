"""운영 지표 집계.

지표 정의(화면에도 같은 문구로 보여 준다):
- AI 관여 대화: 고객이 AI에게 메시지를 한 번 이상 보낸 대화.
- 해결률 = (확인된 해결 + 가정된 해결) ÷ (해결 + 미해결 + 상담원 인계).
  인사뿐인 대화, 고객 답을 기다리는 대화, 판정하지 못한 대화는 분모에서 뺀다.
- 인계율 = 상담원에게 넘어간 대화 ÷ AI 관여 대화.
- 근거 보류: AI 초안이 근거 검사에 걸려 고객에게 나가지 않은 횟수.
- 지연 시간: 고객 메시지를 받은 때부터 AI 응답을 저장할 때까지(서버 안에서 잰 값).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from batondesk.agent.protocol import HANDOFF_REASON_LABEL
from batondesk.store.repo import Repo


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q
    low, high = math.floor(rank), math.ceil(rank)
    if low == high:
        return round(ordered[low], 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (rank - low), 1)


def _dist(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "max": round(max(values), 1) if values else None,
    }


def compute_metrics(repo: Repo, source: str | None = None) -> dict[str, Any]:
    params: tuple[Any, ...] = (source,) if source else ()
    where = "WHERE source = ?" if source else ""
    conversations = repo.db.all(f"SELECT * FROM conversations {where}", params)
    ids = {c["id"] for c in conversations}
    turns = [t for t in repo.db.all("SELECT * FROM turns ORDER BY id") if t["conversation_id"] in ids]
    handoffs = [h for h in repo.db.all("SELECT * FROM handoffs ORDER BY created_at") if h["conversation_id"] in ids]

    involved = [c for c in conversations if c["turn_count"] >= 1 or any(h["conversation_id"] == c["id"] for h in handoffs)]
    statuses = Counter((c["resolution"] or {}).get("status", "unknown") for c in involved)
    resolved = statuses["resolved_confirmed"] + statuses["resolved_assumed"]
    denominator = resolved + statuses["unresolved"] + statuses["handed_off"]
    handed_off_ids = {h["conversation_id"] for h in handoffs}

    reasons = Counter(h["reason"] for h in handoffs)
    waiting = [h for h in handoffs if h["status"] == "waiting"]

    stage: dict[str, list[float]] = {"retrieval": [], "llm_agent": [], "llm_checks": [], "tools": []}
    tool_status: Counter[str] = Counter()
    tool_names: Counter[str] = Counter()
    zero_hit = 0
    doc_use: Counter[str] = Counter()
    for t in turns:
        detail = t["detail"] or {}
        for key, value in (detail.get("stage_latency_ms") or {}).items():
            if key in stage and value:
                stage[key].append(float(value))
        for step in detail.get("steps") or []:
            if step["type"] == "tool":
                tool_status[step["status"]] += 1
                tool_names[step["name"]] += 1
            elif step["type"] == "retrieval" and not step["hits"]:
                zero_hit += 1
        for evidence_id in detail.get("cited") or []:
            doc_use[evidence_id[0]] += 1

    flagged = []
    for t in turns:
        reasons_ = []
        if t["blocked_count"]:
            reasons_.append("근거 검사에 걸린 초안이 있음")
        if t["handoff_reason"] == "ungrounded_answer":
            reasons_.append("근거와 맞는 답을 만들지 못해 인계")
        if t["error"]:
            reasons_.append(f"처리 오류: {t['error']}")
        if t["tool_errors"]:
            reasons_.append("주문 시스템 호출 실패")
        if t["kb_issue"]:
            reasons_.append("지식 문서 점검 요청")
        if reasons_:
            detail = t["detail"] or {}
            flagged.append(
                {
                    "conversation_id": t["conversation_id"],
                    "turn_seq": t["turn_seq"],
                    "started_at": t["started_at"],
                    "reasons": reasons_,
                    "blocked": detail.get("blocked") or [],
                    "kb_issue": t["kb_issue"],
                    "outcome": t["outcome"],
                }
            )

    costs = [t["cost_usd"] for t in turns if t["cost_usd"] is not None]
    return {
        "source": source or "all",
        "conversations": {
            "total": len(conversations),
            "ai_involved": len(involved),
            "open": sum(1 for c in conversations if c["status"] == "open"),
            "handed_off": sum(1 for c in conversations if c["status"] == "handed_off"),
            "closed": sum(1 for c in conversations if c["status"] == "closed"),
        },
        "resolution": {
            "by_status": dict(statuses),
            "resolved_confirmed": statuses["resolved_confirmed"],
            "resolved_assumed": statuses["resolved_assumed"],
            "unresolved": statuses["unresolved"],
            "handed_off": statuses["handed_off"],
            "excluded": len(involved) - denominator,
            "denominator": denominator,
            "rate": round(resolved / denominator, 4) if denominator else None,
        },
        "handoff": {
            "conversations": len(handed_off_ids),
            "rate": round(len(handed_off_ids) / len(involved), 4) if involved else None,
            "waiting": len(waiting),
            "by_reason": [{"reason": r, "label": HANDOFF_REASON_LABEL.get(r, r), "count": n} for r, n in reasons.most_common()],
            "by_source": dict(Counter(h["source"] for h in handoffs)),
        },
        "turns": {
            "total": len(turns),
            "by_outcome": dict(Counter(t["outcome"] for t in turns)),
            "latency_ms": _dist([float(t["latency_ms"]) for t in turns]),
            "stage_latency_ms": {k: _dist(v) for k, v in stage.items()},
            "errors": sum(1 for t in turns if t["error"]),
        },
        "grounding": {
            "by_verdict": dict(Counter(t["grounding_verdict"] or "not_checked" for t in turns)),
            "blocked_drafts": sum(t["blocked_count"] for t in turns),
            "turns_with_revision": sum(1 for t in turns if t["revisions"]),
            "ungrounded_handoffs": sum(1 for t in turns if t["handoff_reason"] == "ungrounded_answer"),
        },
        "llm": {
            "calls": sum(t["llm_calls"] for t in turns),
            "input_tokens": sum(t["input_tokens"] for t in turns),
            "output_tokens": sum(t["output_tokens"] for t in turns),
            "cache_read_tokens": sum(t["cache_read_tokens"] for t in turns),
            "cost_usd": round(sum(costs), 4) if costs else None,
            "by_engine": dict(Counter(t["engine"] for t in turns)),
            "by_model": dict(Counter(t["model"] or "-" for t in turns)),
            "by_prompt": dict(Counter(t["prompt_id"] or "-" for t in turns)),
        },
        "tools": {
            "calls": sum(tool_names.values()),
            "by_status": dict(tool_status),
            "by_name": dict(tool_names),
            "failures": tool_status["timeout"] + tool_status["unavailable"],
        },
        "retrieval": {"zero_hit_turns": zero_hit, "cited_kb": doc_use["K"], "cited_tool": doc_use["T"]},
        "flagged": flagged[-50:][::-1],
    }
