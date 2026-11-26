from __future__ import annotations

import pytest

from batondesk.agent.evidence import Evidence
from batondesk.checks.facts import check_facts, extract_facts
from batondesk.checks.grounding import GroundingChecker
from batondesk.checks.resolution import ResolutionChecker
from batondesk.llm.base import LLMError, LLMRequest, LLMResponse
from batondesk.llm.scripted import QueueLLM, fake_final, fake_response
from batondesk.prompts.registry import PromptRegistry

POLICY = Evidence(
    id="K1", kind="kb", title="반품 정책", turn_seq=1,
    text="상품을 받은 날부터 7일 이내에 반품을 신청할 수 있습니다. 처음에 배송비를 낸 주문은 3,000원(편도)입니다.",
)  # fmt: skip
ORDER = Evidence(
    id="T1", kind="tool", title="get_order", turn_seq=1,
    text='{"order_id": "BM-24011", "paid_total": 42000, "refund_amount": 36000, "shipment": {"delivered_at": "2026-11-17"}}',
)  # fmt: skip


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("반품비는 6,000원입니다", {"amt:6000"}),
        ("3만 원 이상 무료", {"amt:30000"}),
        ("1만 5천원", {"amt:15000"}),
        ("3천원이 붙습니다", {"amt:3000"}),
        ("3~7영업일 걸립니다", {"dur:3bd", "dur:7bd"}),
        ("일주일 안에 신청", {"dur:7d"}),
        ("48시간 이내, 3개월 이내", {"dur:48h", "dur:3mo"}),
        ("2026-11-17에 도착", {"date:2026-11-17", "md:11-17"}),
        ("11월 17일에 도착", {"md:11-17"}),
        ("결제금액의 1%", {"pct:1"}),
        ("오후 2시 이전 결제", {"clock:14:00"}),
        ("주문 bm-24011", {"order:BM-24011"}),
    ],
)
def test_fact_extraction(text: str, expected: set[str]) -> None:
    assert expected <= set(extract_facts(text))


def test_dates_are_not_read_as_durations() -> None:
    facts = set(extract_facts("11월 17일에 받으셨고 7일 이내입니다"))
    assert "dur:17d" not in facts and "dur:7d" in facts


def test_wrong_number_is_flagged_and_right_number_is_not() -> None:
    evidence = [(POLICY.text, False), (ORDER.text, True)]
    assert check_facts("7일 이내에 신청하시면 되고 반품비는 3,000원, 환불 예상 금액은 36,000원입니다.", evidence) == []
    flags = {f.fact: f.hard for f in check_facts("14일 이내에 신청하시면 되고 반품비는 5,000원입니다.", evidence)}
    assert flags == {"dur:14d": False, "amt:5000": True}


def test_facts_the_customer_stated_are_not_flagged() -> None:
    assert check_facts("BM-24099 주문을 찾지 못했습니다.", [], extra_context="BM-24099 주문 확인해 주세요") == []


def _checker(llm: QueueLLM | None, prompts: PromptRegistry) -> GroundingChecker:
    return GroundingChecker(llm, prompts.get("grounding"), "judge-model")


async def test_verdict_is_computed_from_claim_labels(prompts: PromptRegistry) -> None:
    llm = QueueLLM(
        [
            fake_final(
                {
                    "claims": [
                        {"claim": "7일 이내 신청", "label": "supported", "evidence_ids": ["K1"], "reason": "일치"},
                        {"claim": "무료로 회수", "label": "unsupported", "evidence_ids": [], "reason": "근거 없음"},
                        {"claim": "안녕하세요", "label": "not_factual", "evidence_ids": [], "reason": "인사"},
                    ]
                }
            )
        ]
    )
    result, steps = await _checker(llm, prompts).check("안녕하세요. 7일 이내 신청하시면 무료로 회수합니다.", [POLICY])
    assert result.verdict == "ungrounded" and result.mode == "llm+rules"
    assert (result.unsupported, result.contradicted) == (1, 0)
    assert result.problems == ["근거에 없음: 무료로 회수 — 근거 없음"]
    assert result.checker["prompt"] == "grounding-v1" and steps[0]["purpose"] == "grounding"
    request: LLMRequest = llm.requests[0]
    assert "<근거>" in request.messages[0]["content"] and request.output_schema is not None


async def test_judge_failure_falls_back_to_rules_and_fails_closed(prompts: PromptRegistry) -> None:
    def boom(_: LLMRequest) -> LLMResponse:
        raise LLMError("rate limited", kind="rate_limit")

    wrong, steps = await _checker(QueueLLM([boom]), prompts).check("반품비는 5,000원입니다.", [POLICY])
    assert wrong.verdict == "ungrounded" and wrong.mode == "rules_only"
    assert steps[0]["type"] == "check_error"
    right, _ = await _checker(QueueLLM([boom]), prompts).check("반품비는 3,000원입니다.", [POLICY])
    assert right.verdict == "grounded" and right.mode == "rules_only"


async def test_truncated_judge_output_is_not_trusted(prompts: PromptRegistry) -> None:
    llm = QueueLLM([fake_response(text='{"claims": [', stop_reason="max_tokens")])
    result, _ = await _checker(llm, prompts).check("반품비는 3,000원입니다.", [POLICY])
    assert result.mode == "rules_only"


async def test_auto_mode_calls_the_judge_only_when_rules_find_something(prompts: PromptRegistry) -> None:
    llm = QueueLLM()
    clean, _ = await _checker(llm, prompts).check("주문번호를 알려 주시겠어요?", [POLICY], use_llm="auto")
    assert clean.mode == "rules_only" and not llm.requests
    llm.push(fake_final({"claims": [{"claim": "이틀 남음", "label": "supported", "evidence_ids": ["T1"], "reason": "날짜 계산"}]}))
    derived, _ = await _checker(llm, prompts).check("반품 기한이 2일 남았습니다.", [POLICY, ORDER], use_llm="auto")
    assert derived.mode == "llm+rules" and derived.grounded and len(llm.requests) == 1
    assert "2일" in llm.requests[0].messages[0]["content"].split("<참고>")[1]


async def test_resolution_rules_run_before_the_model(prompts: PromptRegistry) -> None:
    llm = QueueLLM()
    checker = ResolutionChecker(llm, prompts.get("resolution"), "judge-model")
    handed, _ = await checker.check([{"role": "고객", "text": "상담원 연결"}], handed_off=True)
    empty, _ = await checker.check([])
    assert handed.status == "handed_off" and handed.counted and not handed.resolved
    assert empty.status == "not_applicable" and not empty.counted
    assert not llm.requests


async def test_resolution_uses_the_model_and_survives_its_failure(prompts: PromptRegistry) -> None:
    messages = [
        {"role": "고객", "text": "반품비 얼마예요?"},
        {"role": "AI", "text": "3,000원입니다."},
        {"role": "고객", "text": "감사합니다"},
    ]
    llm = QueueLLM([fake_final({"status": "resolved_confirmed", "customer_signal": "positive", "reason": "감사 표현"})])
    checker = ResolutionChecker(llm, prompts.get("resolution"), "judge-model")
    result, _ = await checker.check(messages)
    assert result.status == "resolved_confirmed" and result.resolved and result.counted
    assert "고객: 감사합니다" in llm.requests[0].messages[0]["content"]

    failing = ResolutionChecker(QueueLLM([fake_response(text="not json")]), prompts.get("resolution"), "judge-model")
    unknown, steps = await failing.check(messages)
    assert unknown.status == "unknown" and not unknown.counted and steps[-1]["type"] == "check_error"
    none, _ = await ResolutionChecker(None, prompts.get("resolution"), "m").check(messages)
    assert none.status == "unknown"
