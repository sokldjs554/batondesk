"""에이전트 한 턴의 분기. 모델 응답은 대역으로 정해 두고 흐름만 본다."""

from __future__ import annotations

from typing import Any

import pytest

from batondesk.agent.evidence import EvidencePool
from batondesk.agent.graph import HistoryItem, TurnConfig, TurnInput, build_agent_graph, retrieval_queries, run_turn
from batondesk.checks.grounding import GroundingChecker
from batondesk.commerce.store import CommerceStore
from batondesk.llm.base import LLMError, LLMRequest, LLMResponse
from batondesk.llm.scripted import QueueLLM, agent_payload, fake_final, fake_respond, fake_response
from batondesk.prompts.registry import PromptRegistry
from batondesk.tools.executor import ToolExecutor

from .conftest import FIXED_NOW

SUPPORTED = {"claims": [{"claim": "c", "label": "supported", "evidence_ids": ["K1"], "reason": "일치"}]}
CONTRADICTED = {"claims": [{"claim": "14일 이내 반품", "label": "contradicted", "evidence_ids": ["K1"], "reason": "근거는 7일"}]}


@pytest.fixture
def run(executor: ToolExecutor, prompts: PromptRegistry, llm: QueueLLM):  # type: ignore[no-untyped-def]
    async def _run(
        text: str,
        *,
        history: list[HistoryItem] | None = None,
        pool: EvidencePool | None = None,
        customer: str = "C-1001",
        inject_draft: str | None = None,
        **config: Any,
    ):
        cfg = TurnConfig(model="agent-model", prompt=prompts.get("agent"), **config)
        graph = build_agent_graph(llm, executor, GroundingChecker(llm, prompts.get("grounding"), "judge-model"), cfg)
        turn = TurnInput(
            "conv", len(history or []) // 2 + 1, customer, "김하늘", "test", FIXED_NOW, history or [], text,
            pool if pool is not None else EvidencePool(), inject_draft=inject_draft,
        )  # fmt: skip
        return await run_turn(graph, turn)

    return _run


async def test_answer_from_preretrieved_knowledge(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(
        fake_respond(agent_payload("단순변심 반품비는 처음에 배송비를 냈다면 3,000원입니다.", citations=["K1"])), fake_final(SUPPORTED)
    )
    result = await run("반품 배송비 얼마예요?")
    assert result.outcome == "answered" and result.output.citations == ["K1"]
    assert result.grounding is not None and result.grounding.grounded
    assert [s["type"] for s in result.steps] == ["retrieval", "llm", "llm", "grounding"]
    agent_request: LLMRequest = llm.requests[0]
    user_blocks = agent_request.messages[-1]["content"]
    assert "<고객_메시지>" in user_blocks[0]["text"] and "[K1]" in user_blocks[1]["text"]
    assert "오늘: 2026-11-20 (금)" in user_blocks[1]["text"]
    assert agent_request.output_schema is None, "JSON 출력 형식은 쓰지 않는다. 답도 도구(respond)로 제출한다"
    assert len(agent_request.tools) == 8 and agent_request.tools[-1]["name"] == "respond" and agent_request.tools[-1]["strict"] is True


async def test_tool_loop_then_answer(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(
        fake_response(tool_calls=[("get_order", {"order_id": "BM-24011"}), ("track_shipment", {"order_id": "BM-24011"})]),
        fake_respond(agent_payload("BM-24011 주문은 2026-11-17에 배송 완료되었습니다.", citations=["T1", "T2"])),
        fake_final(SUPPORTED),
    )
    result = await run("BM-24011 어디쯤이에요?")
    assert result.outcome == "answered"
    tools = [s for s in result.steps if s["type"] == "tool"]
    assert [(t["name"], t["status"]) for t in tools] == [("get_order", "ok"), ("track_shipment", "ok")]
    second_request = llm.requests[1]
    assert second_request.messages[-2]["role"] == "assistant"
    results = second_request.messages[-1]["content"]
    assert [b["type"] for b in results] == ["tool_result", "tool_result"], "한 응답의 도구 결과는 한 메시지에 모은다"
    assert [b["tool_use_id"] for b in results] == ["toolu_fake_1", "toolu_fake_2"]


async def test_ungrounded_draft_is_held_and_rewritten(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(
        fake_respond(agent_payload("반품은 14일 이내에 가능합니다.", citations=["K1"])),
        fake_final(CONTRADICTED),
        fake_respond(agent_payload("반품은 받은 날부터 7일 이내에 신청하실 수 있습니다.", citations=["K1"])),
        fake_final(SUPPORTED),
    )
    result = await run("반품 언제까지 돼요?")
    assert result.outcome == "answered" and "7일" in result.output.message
    assert result.revisions == 1 and len(result.blocked) == 1 and "14일" in result.blocked[0]["message"]
    feedback = llm.requests[2].messages[-1]["content"][0]
    assert feedback["type"] == "tool_result" and feedback["is_error"] is True and feedback["tool_use_id"] == "toolu_fake_1"
    assert "근거 검사를 통과하지 못해" in feedback["content"] and "근거는 7일" in feedback["content"]


async def test_still_ungrounded_after_rewrite_goes_to_a_human(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(
        fake_respond(agent_payload("반품은 14일 이내에 가능합니다.", citations=["K1"])), fake_final(CONTRADICTED),
        fake_respond(agent_payload("반품은 14일 이내입니다.", citations=["K1"])), fake_final(CONTRADICTED),
    )  # fmt: skip
    result = await run("반품 언제까지 돼요?")
    assert result.outcome == "handoff" and result.handoff_source == "system"
    assert result.output.handoff_reason == "ungrounded_answer"
    assert "14일" not in result.output.message, "걸러진 초안은 고객에게 나가지 않는다"
    assert "근거 검사" in result.output.handoff_summary and len(result.blocked) == 2


async def test_flag_mode_delivers_but_marks(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(fake_respond(agent_payload("반품은 14일 이내에 가능합니다.", citations=["K1"])), fake_final(CONTRADICTED))
    result = await run("반품 언제까지 돼요?", grounding_mode="flag")
    assert result.outcome == "answered" and result.grounding is not None and not result.grounding.grounded


async def test_off_mode_skips_the_check(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(fake_respond(agent_payload("반품은 14일 이내에 가능합니다.", citations=["K1"])))
    result = await run("반품 언제까지 돼요?", grounding_mode="off")
    assert result.outcome == "answered" and result.grounding is None and len(llm.requests) == 1


async def test_unknown_citations_are_removed(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(fake_respond(agent_payload("7일 이내에 신청하실 수 있습니다.", citations=["K1", "K99", "T7"])), fake_final(SUPPORTED))
    result = await run("반품 언제까지 돼요?")
    assert result.output.citations == ["K1"]
    assert {"type": "citation_fix", "removed": ["K99", "T7"]} in result.steps


async def test_model_handoff_keeps_reason_and_summary(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(
        fake_respond(
            agent_payload(
                "보상 금액은 상담원이 확인해 드려야 합니다. 상담원에게 연결해 드리겠습니다.",
                action="handoff", handoff_reason="policy_exception", handoff_summary="고객이 지연 보상 금액 조정을 요구함.",
            )
        )
    )  # fmt: skip
    result = await run("배송 늦었으니 보상 5만원 주세요")
    assert result.outcome == "handoff" and result.handoff_source == "model"
    assert result.output.handoff_reason == "policy_exception"
    assert len(llm.requests) == 1, "근거에 없는 값이 없으면 인계 안내에는 판정 모델을 부르지 않는다"


async def test_non_handoff_output_cannot_carry_a_handoff_reason(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    llm.push(fake_respond(agent_payload("주문번호를 알려 주시겠어요?", action="ask", handoff_reason="tool_failure", handoff_summary="x")))
    result = await run("주문 취소하고 싶어요")
    assert result.outcome == "asked" and result.output.handoff_reason == "none" and result.output.handoff_summary == ""


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (fake_response(text="", stop_reason="refusal"), "refusal:None"),
        (fake_response(text="받은 날부터 7일 이", stop_reason="max_tokens"), "truncated"),
    ],
)
async def test_unusable_model_output_becomes_a_safe_handoff(run, llm: QueueLLM, response: LLMResponse, error: str) -> None:  # type: ignore[no-untyped-def]
    llm.push(response)
    result = await run("반품 언제까지 돼요?")
    assert result.outcome == "handoff" and result.handoff_source == "system"
    assert result.output.handoff_reason == "agent_error" and result.error == error


async def test_text_without_the_respond_tool_is_not_delivered(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    """응답 제출 도구 없이 쓴 글은 근거 검사를 거치지 않았으므로 고객에게 나가지 않는다. 다시 요청한다."""
    llm.push(
        fake_response(text="반품은 14일 이내에 가능합니다."),
        fake_respond(agent_payload("받은 날부터 7일 이내에 신청하실 수 있습니다.", citations=["K1"])),
        fake_final(SUPPORTED),
    )
    result = await run("반품 언제까지 돼요?")
    assert result.outcome == "answered" and "7일" in result.output.message and "14일" not in result.output.message
    assert [s["type"] for s in result.steps].count("nudge") == 1
    assert "respond 도구로 제출" in llm.requests[1].messages[-1]["content"]


async def test_repeated_text_only_turns_end_in_a_safe_handoff(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    for _ in range(3):
        llm.push(fake_response(text="안내드리겠습니다."))
    result = await run("반품 언제까지 돼요?")
    assert result.output.handoff_reason == "agent_error" and result.error == "no_final_response"
    assert "안내드리겠습니다" not in result.output.message


async def test_respond_called_together_with_a_lookup_is_deferred(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    """조회 결과를 보기도 전에 낸 응답은 받지 않는다. 조회만 실행하고 다시 제출하게 한다."""
    llm.push(
        fake_response(tool_calls=[("get_order", {"order_id": "BM-24011"}), ("respond", agent_payload("배송중입니다."))]),
        fake_respond(agent_payload("BM-24011 주문은 배송완료 상태입니다.", citations=["T1"])),
        fake_final(SUPPORTED),
    )
    result = await run("BM-24011 상태 알려주세요")
    assert result.output.message == "BM-24011 주문은 배송완료 상태입니다."
    results = llm.requests[1].messages[-1]["content"]
    assert [(r["tool_use_id"], r["is_error"]) for r in results] == [("toolu_fake_1", False), ("toolu_fake_2", True)]
    assert "결과를 확인한 뒤" in results[1]["content"]


async def test_empty_or_malformed_response_is_rejected_and_retried(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    """실제 API에서 본 실패: 도구를 부르지 않고 빈 message 로 응답을 낸다."""
    llm.push(
        fake_respond(agent_payload("")),
        fake_respond(agent_payload("받은 날부터 7일 이내에 신청하실 수 있습니다.", citations=["K1"])),
        fake_final(SUPPORTED),
    )
    result = await run("반품 언제까지 돼요?")
    assert result.outcome == "answered" and result.error is None
    rejected = llm.requests[1].messages[-1]["content"][0]
    assert rejected["is_error"] is True and "message 를 채워" in rejected["content"]

    llm.push(fake_respond({"action": "dance", "message": "x"}), fake_respond({"action": "dance", "message": "x"}))
    broken = await run("반품 언제까지 돼요?", max_tool_rounds=1)
    assert broken.output.handoff_reason == "agent_error" and broken.error == "bad_output"


async def test_model_api_failure_becomes_a_safe_handoff(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    def boom(_: LLMRequest) -> LLMResponse:
        raise LLMError("overloaded", kind="server", attempts=3)

    llm.push(boom)
    result = await run("반품 언제까지 돼요?")
    assert result.output.handoff_reason == "agent_error" and result.error == "llm_server"
    assert result.steps[-2]["type"] == "llm_error" and result.steps[-2]["attempts"] == 3


async def test_tool_round_limit(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    for _ in range(3):
        llm.push(fake_response(tool_calls=[("list_my_orders", {})]))
    result = await run("주문 확인", max_tool_rounds=2)
    assert result.output.handoff_reason == "agent_error" and result.error == "tool_round_limit"
    assert len([s for s in result.steps if s["type"] == "tool"]) == 2


async def test_tool_failure_reaches_the_model_as_an_error_result(run, llm: QueueLLM, commerce_store: CommerceStore) -> None:  # type: ignore[no-untyped-def]
    commerce_store.sandbox("test").fault = "error"
    llm.push(
        fake_response(tool_calls=[("get_order", {"order_id": "BM-24011"})]),
        fake_respond(
            agent_payload(
                "주문 시스템 확인이 어려워 상담원에게 연결해 드리겠습니다.",
                action="handoff",
                handoff_reason="tool_failure",
                handoff_summary="get_order 실패",
            )
        ),
    )
    result = await run("BM-24011 상태 알려주세요")
    tool_result = llm.requests[1].messages[-1]["content"][0]
    assert tool_result["is_error"] is True and "unavailable" in tool_result["content"]
    assert result.output.handoff_reason == "tool_failure"


async def test_history_and_carried_evidence_reach_the_next_turn(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    pool = EvidencePool()
    llm.push(
        fake_response(tool_calls=[("get_order", {"order_id": "BM-24011"})]),
        fake_respond(agent_payload("BM-24011 주문은 배송완료 상태입니다.", citations=["T1", "K1"])),
        fake_final(SUPPORTED),
    )
    first = await run("BM-24011 상태 알려주세요", pool=pool)
    llm.push(fake_respond(agent_payload("받은 날부터 7일 이내에 신청하실 수 있습니다.", citations=["K1"])), fake_final(SUPPORTED))
    history = [HistoryItem("customer", "BM-24011 상태 알려주세요"), HistoryItem("ai", first.output.message)]
    await run("그럼 반품은요?", history=history, pool=pool)
    request = llm.requests[-2]
    assert [m["role"] for m in request.messages] == ["user", "assistant", "user"]
    context = request.messages[-1]["content"][1]["text"]
    assert "이전 턴까지 확보한 근거:" in context and "[T1]" in context


async def test_injected_draft_must_pass_the_same_check(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    """평가용 주입: 모델의 멀쩡한 초안을 틀린 글로 바꿔도 근거 검사가 막아야 한다."""
    llm.push(
        fake_respond(agent_payload("받은 날부터 7일 이내에 신청하실 수 있습니다.", citations=["K1"])),
        fake_final(CONTRADICTED),
        fake_respond(agent_payload("받은 날부터 7일 이내에 신청하실 수 있습니다.", citations=["K1"])),
        fake_final(SUPPORTED),
    )
    result = await run("반품 언제까지 돼요?", inject_draft="반품은 받은 날부터 14일 이내에 신청하실 수 있습니다.")
    assert result.outcome == "answered" and "7일" in result.output.message
    assert result.blocked[0]["message"].startswith("반품은 받은 날부터 14일")
    assert [s["type"] for s in result.steps].count("eval_injection") == 1, "주입은 첫 초안에만 한 번"


def test_followup_adds_a_second_query_with_the_previous_message() -> None:
    history = [HistoryItem("customer", "무료배송으로 받은 가습기 반품하려고요"), HistoryItem("ai", "안내드립니다")]
    assert retrieval_queries(history, "그럼 비용은요?") == ["그럼 비용은요?", "무료배송으로 받은 가습기 반품하려고요 그럼 비용은요?"]
    long_question = "카드로 결제한 주문을 취소하면 환불은 며칠 뒤에 들어오나요?"
    assert retrieval_queries(history, long_question) == [long_question]
    assert retrieval_queries([], "그럼 비용은요?") == ["그럼 비용은요?"]


async def test_topic_switch_still_retrieves_for_the_new_question(run, llm: QueueLLM) -> None:  # type: ignore[no-untyped-def]
    """짧은 새 질문에 앞 메시지를 붙이기만 하면 앞 화제의 문서가 검색을 차지한다."""
    llm.push(fake_respond(agent_payload("제주는 3,000원이 추가됩니다.", citations=["K1"])), fake_final(SUPPORTED))
    history = [HistoryItem("customer", "단순변심 반품 배송비는 누가 내나요?"), HistoryItem("ai", "고객님이 부담하십니다.")]
    result = await run("제주 추가 배송비는요?", history=history)
    hits = result.steps[0]["hits"]
    assert hits[0]["chunk_id"].startswith("shipping-remote"), "새 질문만으로 한 검색의 1위가 맨 앞에 온다"
    assert any(h["chunk_id"].startswith("return-policy") for h in hits), "앞 화제를 붙인 검색 결과도 함께 준다"
    assert len(hits) <= 7
