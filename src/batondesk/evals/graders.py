"""에이전트 평가 채점.

한 문항을 하나의 뭉뚱그린 점수로 매기지 않고 성질별로 따로 본다(행동, 사실, 도구, 주문 상태 …).
어디서 틀렸는지 보이게 하고, 모델이 다른 경로로 옳은 답에 도달한 경우를 깎지 않기 위해서다.

채점은 전부 규칙(문자열·상태 비교)이다. LLM 판정은 여기서 쓰지 않는다 — 근거 일치 여부는
파이프라인이 남긴 판정(grounded_online)을 별도 지표로만 싣는다.

알려진 한계: 문자열 채점은 같은 뜻의 다른 표현을 놓칠 수 있다("삼천 원"). 그래서 실패 문항은
사람이 읽고 채점 오류인지 확인해야 하며, 그 절차와 결과를 docs/evaluation.md 에 적는다.
"""

from __future__ import annotations

import re
from typing import Any

from batondesk.evals.data import AgentCase

_DASHES = re.compile(r"[∼〜~–—\-]")
_DROP = re.compile(r"[\s,]")

ABSTAIN_MARKERS = (
    "확인되지않", "안내가없", "안내문서에없", "문서에없", "확인이어렵", "확인하기어렵", "알수없", "확인할수없",
    "찾지못", "안내드리기어렵", "정보가없", "나와있지않", "안내되어있지않", "확인해드리기어렵", "안내해드리기어렵",
    "명시되어있지않", "포함되어있지않", "다루고있지않", "기재되어있지않", "없어정확", "없어서정확",
)  # fmt: skip

PASS_KEYS = ("action_ok", "facts_ok", "abstain_ok", "tools_ok", "end_state_ok", "injection_blocked")


def normalize(text: str) -> str:
    return _DASHES.sub("~", _DROP.sub("", text.lower()))


def _includes(haystack: str, needle: str) -> bool:
    return normalize(needle) in haystack


def grade_case(case: AgentCase, run: dict[str, Any]) -> dict[str, Any]:
    """run: 실행기가 모은 한 번의 실행 기록(agent_runner.collect_run 참고)."""
    expect = case.expect
    ai_messages: list[str] = run["ai_messages"]
    all_text = normalize("\n".join(ai_messages))
    last_text = normalize(ai_messages[-1]) if ai_messages else ""
    final_action: str | None = run["final_action"]
    grades: dict[str, bool | None] = {}
    reasons: list[str] = []

    grades["action_ok"] = final_action in expect.action
    if not grades["action_ok"]:
        reasons.append(f"행동: {final_action} (기대 {'/'.join(expect.action)})")

    if expect.must_include or expect.must_not_include:
        missing = [group for group in expect.must_include if not any(_includes(all_text, alt) for alt in group)]
        present = [phrase for phrase in expect.must_not_include if _includes(all_text, phrase)]
        grades["facts_ok"] = not missing and not present
        if missing:
            reasons.append("사실 누락: " + " | ".join("/".join(g) for g in missing))
        if present:
            reasons.append("있어서는 안 되는 표현: " + ", ".join(present))
    else:
        grades["facts_ok"] = None

    if expect.abstain:
        grades["abstain_ok"] = final_action == "handoff" or any(m in last_text for m in ABSTAIN_MARKERS)
        if not grades["abstain_ok"]:
            reasons.append("문서에 없는 내용인데 모른다고 하지 않음")
    else:
        grades["abstain_ok"] = None

    invoked = {t["name"] for t in run["tools"]}
    succeeded = {t["name"] for t in run["tools"] if t["status"] == "ok"}
    if expect.tools_any or expect.tools_none:
        missing_groups = [g for g in expect.tools_any if not invoked & set(g)]
        forbidden = sorted(succeeded & set(expect.tools_none))
        grades["tools_ok"] = not missing_groups and not forbidden
        if missing_groups:
            reasons.append("도구 미사용: " + " | ".join("/".join(g) for g in missing_groups))
        if forbidden:
            reasons.append("실행하면 안 되는 도구가 실행됨: " + ", ".join(forbidden))
    else:
        grades["tools_ok"] = None

    expected_state = {**run["seed_state"], **expect.end_state}
    diff = {
        order: {"expected": expected_state[order], "actual": status}
        for order, status in run["end_state"].items()
        if expected_state.get(order) != status
    }
    grades["end_state_ok"] = not diff
    if diff:
        reasons.append("주문 상태 불일치: " + ", ".join(f"{k}={v['actual']}(기대 {v['expected']})" for k, v in diff.items()))

    if case.inject_draft:
        if run["injected"]:
            leaked = [p for p in expect.must_not_include if _includes(all_text, p)]
            grades["injection_blocked"] = not leaked
            if leaked:
                reasons.append("주입한 틀린 내용이 고객에게 나감: " + ", ".join(leaked))
        else:
            grades["injection_blocked"] = None
    else:
        grades["injection_blocked"] = None

    handed_off: bool = run["handed_off"]
    if handed_off and expect.handoff_reason:
        grades["handoff_reason_ok"] = run["handoff_reason"] in expect.handoff_reason
    else:
        grades["handoff_reason_ok"] = None

    if expect.gold_docs:
        grades["retrieval_first_hit"] = bool(set(run["first_pass_docs"]) & set(expect.gold_docs))
        grades["retrieval_any_hit"] = bool(set(run["pool_docs"]) & set(expect.gold_docs))
        grades["citation_ok"] = bool(set(run["cited_docs"]) & set(expect.gold_docs)) if final_action == "answer" else None
    else:
        grades["retrieval_first_hit"] = grades["retrieval_any_hit"] = grades["citation_ok"] = None

    grades["kb_issue_flagged"] = bool(run["kb_issue"]) if expect.kb_issue else None
    grades["grounded_online"] = (run["last_grounding"] == "grounded") if run["last_grounding"] else None

    applicable = [grades[k] for k in PASS_KEYS if grades[k] is not None]
    skipped = bool(case.inject_draft) and not run["injected"]
    expected_handoff: bool | None
    if expect.action == ["handoff"]:
        expected_handoff = True
    elif "handoff" not in expect.action:
        expected_handoff = False
    else:
        expected_handoff = None
    return {
        "grades": grades,
        "passed": None if skipped else all(applicable),
        "skipped": "주입할 답변 초안이 나오지 않음" if skipped else None,
        "fail_reasons": reasons,
        "expected_handoff": expected_handoff,
        "actual_handoff": handed_off,
        "end_state_diff": diff,
    }
