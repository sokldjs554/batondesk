from __future__ import annotations

import json

from batondesk.agent.evidence import EvidencePool
from batondesk.commerce.store import CommerceStore
from batondesk.tools.executor import ToolContext, ToolExecutor
from batondesk.tools.specs import TOOL_NAMES, tool_definitions

from .conftest import FIXED_NOW


def _ctx(customer: str = "C-1002", turn: int = 1) -> ToolContext:
    return ToolContext(customer_id=customer, sandbox_id="test", turn_seq=turn, today=FIXED_NOW.date(), pool=EvidencePool())


def test_tool_schemas_are_strict() -> None:
    tools = tool_definitions()
    assert [t["name"] for t in tools] == list(TOOL_NAMES)
    for tool in tools:
        schema = tool["input_schema"]
        assert tool["strict"] is True and schema["additionalProperties"] is False
        assert sorted(schema["required"]) == sorted(schema["properties"])


def test_prompt_version_can_override_tool_descriptions() -> None:
    tools = {t["name"]: t for t in tool_definitions({"get_order": "바뀐 설명"})}
    assert tools["get_order"]["description"] == "바뀐 설명"
    assert tools["track_shipment"]["description"] != "바뀐 설명"


async def test_unknown_tool_is_denied(executor: ToolExecutor) -> None:
    result = await executor.run("delete_order", {"order_id": "BM-24011"}, _ctx())
    assert result.status == "denied" and result.is_error


async def test_order_id_is_validated_before_calling_the_system(executor: ToolExecutor) -> None:
    result = await executor.run("get_order", {"order_id": "24011 OR 1=1"}, _ctx())
    assert result.status == "invalid" and result.is_error and "형식" in result.content


async def test_order_lookup_is_scoped_to_the_conversation_customer(executor: ToolExecutor) -> None:
    ctx = _ctx("C-1002")
    result = await executor.run("get_order", {"order_id": "bm-24011"}, ctx)
    assert result.status == "not_found" and not result.is_error
    assert json.loads(result.content)["result"]["found"] is False
    assert ctx.pool.get(result.evidence_ids[0]) is not None, "'찾지 못함'도 답변의 근거로 남는다"


async def test_search_registers_evidence_once(executor: ToolExecutor) -> None:
    ctx = _ctx()
    first = await executor.run("search_knowledge", {"query": "반품 배송비"}, ctx)
    second = await executor.run("search_knowledge", {"query": "반품 배송비 얼마"}, ctx)
    assert first.evidence_ids and set(first.evidence_ids) & set(second.evidence_ids)
    ids = [e.id for e in ctx.pool.all()]
    assert len(ids) == len(set(ids))


async def test_write_needs_a_code_from_an_earlier_turn(executor: ToolExecutor, commerce_store: CommerceStore) -> None:
    ctx = _ctx("C-1002", turn=1)
    check = await executor.run("check_cancellation", {"order_id": "BM-24022"}, ctx)
    code = json.loads(check.content)["result"]["confirmation_code"]

    same_turn = await executor.run("execute_confirmed_action", {"confirmation_code": code}, ctx)
    assert same_turn.status == "denied"
    assert commerce_store.sandbox("test").orders["BM-24022"]["status"] == "paid"

    made_up = await executor.run("execute_confirmed_action", {"confirmation_code": "CF-000000"}, ctx)
    assert made_up.status == "denied"

    ctx.turn_seq = 2
    executed = await executor.run("execute_confirmed_action", {"confirmation_code": code}, ctx)
    assert executed.status == "ok"
    assert commerce_store.sandbox("test").orders["BM-24022"]["status"] == "cancelled"

    again = await executor.run("execute_confirmed_action", {"confirmation_code": code}, ctx)
    assert again.status == "denied", "한 번 쓴 코드는 다시 쓸 수 없다"


async def test_no_code_when_the_action_is_not_allowed(executor: ToolExecutor) -> None:
    ctx = _ctx("C-1001")
    result = await executor.run("check_cancellation", {"order_id": "BM-24012"}, ctx)
    body = json.loads(result.content)["result"]
    assert body["allowed"] is False and "confirmation_code" not in body and not ctx.pending


async def test_return_needing_human_review_gets_no_code(executor: ToolExecutor) -> None:
    ctx = _ctx("C-1001")
    result = await executor.run("check_return", {"order_id": "BM-24014", "item_id": "I-1", "reason": "change_of_mind"}, ctx)
    body = json.loads(result.content)["result"]
    assert body["needs_agent_review"] is True and "confirmation_code" not in body


async def test_return_is_filed_after_confirmation(executor: ToolExecutor, commerce_store: CommerceStore) -> None:
    ctx = _ctx("C-1001")
    check = await executor.run("check_return", {"order_id": "BM-24011", "item_id": "I-1", "reason": "change_of_mind"}, ctx)
    body = json.loads(check.content)["result"]
    assert (body["return_fee"], body["refund_amount"]) == (3000, 36000)
    ctx.turn_seq = 2
    done = await executor.run("execute_confirmed_action", {"confirmation_code": body["confirmation_code"]}, ctx)
    assert done.status == "ok"
    assert commerce_store.sandbox("test").snapshot()["BM-24011"] == {"status": "return_requested", "has_return_request": True}


async def test_system_failure_is_reported_to_the_model_as_an_error(executor: ToolExecutor, commerce_store: CommerceStore) -> None:
    commerce_store.sandbox("test").fault = "error"
    ctx = _ctx("C-1001")
    result = await executor.run("get_order", {"order_id": "BM-24011"}, ctx)
    assert result.is_error and result.status == "unavailable" and result.attempts == 2
    assert "추측" in result.content
    # 실패도 근거 묶음에 남는다: 에이전트가 본 오류 문구를 근거 판정기도 볼 수 있어야 한다.
    (recorded,) = ctx.pool.all()
    assert recorded.meta["status"] == "unavailable" and result.evidence_ids == [recorded.id]
    assert json.loads(recorded.text)["error"] == "unavailable"


async def test_invalid_order_id_message_is_kept_as_evidence(executor: ToolExecutor) -> None:
    ctx = _ctx()
    result = await executor.run("get_order", {"order_id": "A-778"}, ctx)
    assert result.status == "invalid" and result.is_error
    (recorded,) = ctx.pool.all()
    assert "형식" in recorded.text and recorded.meta["status"] == "invalid"


async def test_a_notice_hit_brings_the_rest_of_that_notice(executor: ToolExecutor) -> None:
    """공지의 한 절만 걸려도 같은 공지의 나머지 절이 근거에 함께 들어온다(개발용 A-D-CF-04 에서 나온 수정)."""
    ctx = _ctx()
    found = executor.search("지금 주문하면 출고되고 나서 며칠 만에 받아요?", ctx)
    by_chunk = {e.meta["chunk_id"]: e for e in found}
    assert "notice-2026-winter-shipping#2" in by_chunk, "전제: 이 질의는 공지의 '출고 시점' 절을 찾는다"
    sibling = by_chunk["notice-2026-winter-shipping#1"]
    assert "1~2영업일" in sibling.text
    ranked = [e for e in found if e.meta["expanded_from"] is None]
    assert len(ranked) == ctx.retrieval_k, "검색 순위로 들어온 근거의 수는 그대로다"
    # 공지가 아닌 문서는 넓히지 않는다
    policy_docs = {e.meta["doc_id"] for e in found if e.meta["doc_type"] != "notice"}
    for doc_id in policy_docs:
        in_pool = [e for e in found if e.meta["doc_id"] == doc_id]
        assert all(e.meta["expanded_from"] is None for e in in_pool)
