from __future__ import annotations

import pytest

from batondesk.agent.evidence import Evidence
from batondesk.checks.facts import check_facts, extract_facts
from batondesk.checks.grounding import GroundingChecker
from batondesk.checks.resolution import ResolutionChecker
from batondesk.llm.base import LLMError, LLMRequest, LLMResponse
from batondesk.llm.scripted import QueueLLM, fake_final, fake_response
from batondesk.prompts.registry import PromptRegistry

from .conftest import ACTIVE

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
    assert flags == {"dur:14d": True, "amt:5000": True}


@pytest.mark.parametrize(
    ("answer", "evidence", "unsupported"),
    [
        ("반품은 14일 이내입니다.", "반품은 7일 이내입니다.", "dur:14d"),
        ("반품은 보름 이내입니다.", "반품은 일주일 이내입니다.", "dur:15d"),
        ("환불은 3~8영업일 걸립니다.", "환불은 3~7영업일 걸립니다.", "dur:8bd"),
        ("환불은 3영업일~8영업일 걸립니다.", "환불은 3~7영업일 걸립니다.", "dur:8bd"),
        ("처리는 3~8일 걸립니다.", "처리는 3~7일 걸립니다.", "dur:8d"),
        ("처리는 3일~8일 걸립니다.", "처리는 3~7일 걸립니다.", "dur:8d"),
        ("접수는 48시간 이내입니다.", "접수는 24시간 이내입니다.", "dur:48h"),
        ("보증은 한 달입니다.", "보증은 3개월입니다.", "dur:1mo"),
        ("보증은 2년입니다.", "보증은 1년입니다.", "dur:2y"),
    ],
)
def test_unsupported_policy_durations_are_hard(answer: str, evidence: str, unsupported: str) -> None:
    flags = check_facts(answer, [(evidence, False)])
    assert [(f.fact, f.hard) for f in flags] == [(unsupported, True)]


@pytest.mark.parametrize(
    ("answer", "evidence"),
    [
        ("반품은 일주일 이내입니다.", "반품은 7일 이내입니다."),
        ("반품은 7일 이내입니다.", "반품은 일주일 이내입니다."),
        ("환불은 3~7영업일 걸립니다.", "환불은 3~7영업일 걸립니다."),
        ("처리는 3~7일 걸립니다.", "처리는 3~7일 걸립니다."),
        ("보증은 1개월입니다.", "보증은 한 달입니다."),
    ],
)
def test_supported_policy_duration_aliases_and_ranges_are_not_flagged(answer: str, evidence: str) -> None:
    assert check_facts(answer, [(evidence, False)]) == []


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("반품 기한이 이틀 남았습니다.", {"dur:2d"}),
        ("반품 기한이 2일남았습니다.", {"dur:2d"}),
        ("반품 기한이 2일이 남았습니다.", {"dur:2d"}),
        ("반품 기한이 2일 정도 남았습니다.", {"dur:2d"}),
        ("받으신 지 2일이 지났습니다.", {"dur:2d"}),
        ("받으신 지 2일이 경과했습니다.", {"dur:2d"}),
        ("기한이 하루 지난 상태입니다.", {"dur:1d"}),
        ("남은 기간은 2일입니다.", {"dur:2d"}),
        ("남은 기간은 이틀입니다.", {"dur:2d"}),
        ("환불까지 2~3영업일 남았습니다.", {"dur:2bd", "dur:3bd"}),
        ("환불까지 2영업일~3영업일 남았습니다.", {"dur:2bd", "dur:3bd"}),
        ("기한까지 2~3일 남았습니다.", {"dur:2d", "dur:3d"}),
        ("기한까지 2일~3일 남았습니다.", {"dur:2d", "dur:3d"}),
    ],
)
def test_derived_durations_still_raise_soft_flags(answer: str, expected: set[str]) -> None:
    flags = check_facts(answer, [(POLICY.text, False), (ORDER.text, True)])
    assert {f.fact for f in flags} == expected
    assert not any(f.hard for f in flags)


@pytest.mark.parametrize(
    "answer",
    [
        "반품은 14일 이내이고 오늘 기준 이틀 남았습니다.",
        "오늘 기준 이틀 남았고 반품은 14일 이내입니다.",
        "반품 기한이 14일 남았습니다. 반품은 14일 이내입니다.",
        "반품은 14일 이내이고 기한은 14일 남았습니다.",
        "남은 기간은 14일이며 반품은 14일 이내입니다.",
    ],
)
def test_derived_duration_does_not_excuse_another_policy_occurrence(answer: str) -> None:
    flags = check_facts(answer, [(POLICY.text, False)])
    assert {f.fact for f in flags if f.hard} == {"dur:14d"}


@pytest.mark.parametrize("limit", ["이내에", "안에", "내에"])
def test_past_duration_word_does_not_excuse_a_policy_deadline(limit: str) -> None:
    flags = check_facts(f"지난 14일 {limit} 산 제품은 반품 가능합니다.", [(POLICY.text, False)])
    assert [(f.fact, f.hard) for f in flags] == [("dur:14d", True)]


@pytest.mark.parametrize(
    "condition", ["지나면", "지났으면", "지난 경우", "지나고 나면", "경과하면", "경과했으면", "경과한 경우", "경과할 때"]
)
def test_elapsed_condition_is_a_policy_cutoff(condition: str) -> None:
    flags = check_facts(f"상품을 받은 지 14일이 {condition} 반품할 수 없습니다.", [(POLICY.text, False)])
    assert [(f.fact, f.hard) for f in flags] == [("dur:14d", True)]


def test_countdown_does_not_excuse_same_value_in_conditional_policy() -> None:
    answer = "기한이 14일 남았습니다. 상품을 받은 지 14일이 지나면 반품할 수 없습니다."
    flags = check_facts(answer, [(POLICY.text, False)])
    assert [(f.fact, f.hard) for f in flags] == [("dur:14d", True)]


@pytest.mark.parametrize(
    ("answer", "surface"),
    [
        ("보름 남았습니다. 반품은 15일 이내입니다.", "15일"),
        ("15일 남았습니다. 반품은 보름 이내입니다.", "보름"),
    ],
)
def test_hard_duration_flag_uses_the_policy_surface_when_alias_value_is_repeated(answer: str, surface: str) -> None:
    flags = check_facts(answer, [(POLICY.text, False)])
    assert [(f.fact, f.surface, f.hard) for f in flags] == [("dur:15d", surface, True)]


@pytest.mark.parametrize("date", ["2026-11-17", "2026년 11월 17일", "11월 17일", "11월17일"])
def test_calendar_dates_do_not_create_hard_duration_flags(date: str) -> None:
    facts = extract_facts(f"{date}에 받으셨고 반품은 7일 이내입니다.")
    assert {f for f in facts if f.startswith("dur:")} == {"dur:7d"}
    flags = check_facts(f"{date}에 받으셨고 기한까지 이틀 남았습니다.", [(f"배송일: {date}", False)])
    assert [(f.fact, f.hard) for f in flags] == [("dur:2d", False)]


@pytest.mark.parametrize("label", ["환불 예정일은", "배송일은", "날짜는"])
def test_bare_calendar_day_stays_soft_without_inferring_month_or_year(label: str) -> None:
    flags = check_facts(f"{label} 20일입니다.", [(f"{label} 2026-11-20입니다.", False)])
    assert [(f.fact, f.hard) for f in flags] == [("dur:20d", False)]


@pytest.mark.parametrize(
    ("answer", "fact"),
    [
        ("반품 기간은 20일입니다.", "dur:20d"),
        ("환불 예정일은 20일 후입니다.", "dur:20d"),
        ("환불 예정일은 20영업일입니다.", "dur:20bd"),
        ("환불 예정일은 40일입니다.", "dur:40d"),
        ("환불 예정일은 20일입니다. 반품 기간은 20일입니다.", "dur:20d"),
    ],
)
def test_calendar_context_does_not_excuse_policy_durations(answer: str, fact: str) -> None:
    flags = check_facts(answer, [(POLICY.text, False), ("환불 예정일은 2026-11-20입니다.", False)])
    assert [(f.fact, f.hard) for f in flags] == [(fact, True)]


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
    assert result.checker["prompt"] == ACTIVE["grounding"] and steps[0]["purpose"] == "grounding"
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


@pytest.mark.parametrize("judge_fails", [False, True])
@pytest.mark.parametrize(
    ("answer", "grounded"),
    [
        ("반품은 14일 이내입니다.", False),
        ("반품은 7일 이내입니다.", True),
        ("반품 기한이 이틀 남았습니다.", True),
        ("반품 기한이 2일 남았습니다.", True),
        ("상품을 받은 지 14일이 지나면 반품할 수 없습니다.", False),
    ],
)
async def test_policy_durations_and_countdowns_in_rules_fallback(
    prompts: PromptRegistry, answer: str, grounded: bool, judge_fails: bool
) -> None:
    def boom(_: LLMRequest) -> LLMResponse:
        raise LLMError("rate limited", kind="rate_limit")

    checker = _checker(QueueLLM([boom]) if judge_fails else None, prompts)
    result, steps = await checker.check(answer, [POLICY, ORDER], use_llm=judge_fails)
    assert result.mode == "rules_only" and result.grounded is grounded
    assert bool(steps) is judge_fails


async def test_bare_refund_calendar_day_is_not_blocked_in_rules_only_mode(prompts: PromptRegistry) -> None:
    evidence = Evidence(id="K2", kind="kb", title="환불 안내", turn_seq=1, text="환불 예정일은 2026-11-20입니다.")
    result, _ = await _checker(None, prompts).check("환불 예정일은 20일입니다.", [evidence], use_llm=False)
    assert result.grounded and result.mode == "rules_only"
    assert [(flag["fact"], flag["hard"]) for flag in result.fact_flags] == [("dur:20d", False)]


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


async def test_today_is_given_to_the_judge_and_to_the_rules(prompts: PromptRegistry) -> None:
    from datetime import datetime

    from batondesk.clock import KST

    today = datetime(2026, 11, 20, 10, 0, tzinfo=KST)
    llm = QueueLLM([fake_final({"claims": []})])
    await _checker(llm, prompts).check("김하늘 고객님, 오늘 기준으로 4일 남았습니다.", [ORDER], today=today, customer_name="김하늘")
    sent = llm.requests[0].messages[0]["content"]
    assert "<상담_정보>지금: 2026-11-20 (금) 10:00 KST, 11월 20일 · 고객 이름: 김하늘 (로그인한 고객)</상담_정보>" in sent
    flags = check_facts("오늘 11월 20일 오전 10시 기준으로 안내드립니다.", [], extra_context="지금: 2026-11-20 (금) 10:00 KST, 11월 20일")
    assert flags == []


def test_tool_json_zero_amounts_and_24h_times_are_known_facts() -> None:
    tool = '{"return_fee": 0, "refund_amount": 124000, "payment_due_at": "2026-11-21 05:12", "items": [{"qty": 2}]}'
    answer = "반품 배송비는 0원이고 124,000원이 환불됩니다. 입금은 11월 21일 오전 5시 12분까지입니다."
    assert check_facts(answer, [(tool, True)]) == []
    assert {f.surface for f in check_facts("반품 배송비는 500원입니다.", [(tool, True)])} == {"500원"}
