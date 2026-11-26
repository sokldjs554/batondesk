from __future__ import annotations

import re
from datetime import timedelta

import pytest

from batondesk.commerce import policy
from batondesk.commerce.store import CommerceStore
from batondesk.kb.retriever import KnowledgeBase
from batondesk.tools.client import CommerceClient

from .conftest import FIXED_NOW


def _order(store: CommerceStore, order_id: str) -> dict:
    return store.sandbox("test").orders[order_id]


def test_cancel_rules(commerce_store: CommerceStore) -> None:
    assert policy.decide_cancel(_order(commerce_store, "BM-24022")).allowed
    assert policy.decide_cancel(_order(commerce_store, "BM-24022")).details["refund_amount"] == 74000
    assert policy.decide_cancel(_order(commerce_store, "BM-24012")).code == "already_shipped"
    assert policy.decide_cancel(_order(commerce_store, "BM-24013")).code == "custom_in_production"
    assert policy.decide_cancel(_order(commerce_store, "BM-24025")).code == "already_cancelled"
    assert policy.decide_cancel(_order(commerce_store, "BM-24033")).details["refund_amount"] == 0


@pytest.mark.parametrize(
    ("order_id", "reason", "allowed", "code", "fee", "refund"),
    [
        ("BM-24011", "change_of_mind", True, "returnable_change_of_mind", 3000, 36000),
        ("BM-24021", "change_of_mind", True, "returnable_change_of_mind", 6000, 83000),
        ("BM-24021", "defective", True, "returnable_fault", 0, 89000),
        ("BM-24015", "change_of_mind", False, "return_window_passed", None, None),
        ("BM-24031", "change_of_mind", False, "return_window_passed", None, None),
        ("BM-24034", "wrong_item", True, "returnable_fault", 0, 124000),
        ("BM-24016", "change_of_mind", False, "fresh_no_change_of_mind", None, None),
        ("BM-24016", "defective", True, "fresh_quality_claim", 0, None),
        ("BM-24014", "change_of_mind", True, "furniture_fee_unconfirmed", None, None),
        ("BM-24012", "change_of_mind", False, "not_delivered", None, None),
        ("BM-24035", "change_of_mind", False, "return_in_progress", None, None),
    ],
)
def test_return_rules(
    commerce_store: CommerceStore, order_id: str, reason: str, allowed: bool, code: str, fee: int | None, refund: int | None
) -> None:
    sandbox = commerce_store.sandbox("test")
    order = sandbox.orders[order_id]
    decision = policy.decide_return(order, "I-1", reason, sandbox.now(), sandbox.delivered_at(order))  # type: ignore[arg-type]
    assert (decision.allowed, decision.code) == (allowed, code)
    assert decision.details.get("return_fee") == fee
    assert decision.details.get("refund_amount") == refund


def test_return_window_counts_from_the_day_after_delivery(commerce_store: CommerceStore) -> None:
    sandbox = commerce_store.sandbox("test")
    order = sandbox.orders["BM-24011"]
    delivered = sandbox.delivered_at(order)
    assert delivered is not None
    last_day = (delivered + timedelta(days=7)).replace(hour=23, minute=0)
    assert policy.decide_return(order, "I-1", "change_of_mind", last_day, delivered).allowed
    assert not policy.decide_return(order, "I-1", "change_of_mind", last_day + timedelta(hours=2), delivered).allowed


def test_policy_numbers_match_knowledge_documents(kb: KnowledgeBase) -> None:
    """판정 규칙의 숫자가 지식 문서에 적힌 숫자와 어긋나면 에이전트가 서로 다른 두 답을 갖게 된다."""

    def text(doc_id: str) -> str:
        return " ".join(c.text for c in kb.chunks_of(doc_id))

    returns = text("return-policy")
    assert f"{policy.RETURN_WINDOW_DAYS}일 이내" in returns
    assert f"{policy.RETURN_FEE_ONE_WAY:,}원(편도)" in returns and f"{policy.RETURN_FEE_ROUND_TRIP:,}원(왕복)" in returns
    assert "3개월 이내" in returns and policy.FAULT_RETURN_WINDOW_DAYS == 90
    shipping = text("shipping-policy")
    assert f"{policy.BASE_SHIPPING_FEE:,}원" in shipping and f"{policy.FREE_SHIPPING_THRESHOLD:,}원 이상" in shipping
    assert f"{policy.FRESH_CLAIM_WINDOW_HOURS}시간 이내" in text("fresh-food")
    assert f"{policy.ELECTRONICS_DOA_DAYS}일 이내" in text("defective-items")
    assert re.search(r"회수비 30,000원", text("furniture-delivery")) and re.search(r"회수비 20,000원", returns), (
        "가구 회수비 불일치는 '문서 충돌' 평가 문항을 위해 일부러 둔 것이다"
    )


async def test_api_hides_other_customers_orders(commerce: CommerceClient) -> None:
    mine = await commerce.call("GET", "/orders/BM-24011", sandbox_id="test", customer_id="C-1001")
    theirs = await commerce.call("GET", "/orders/BM-24011", sandbox_id="test", customer_id="C-1002")
    missing = await commerce.call("GET", "/orders/BM-99999", sandbox_id="test", customer_id="C-1001")
    assert mine.ok and mine.data["status_label"] == "배송완료" and mine.data["paid_total"] == 42000
    assert theirs.status == "not_found" and missing.status == "not_found"
    assert theirs.error == missing.error


async def test_dates_resolve_against_the_fixed_clock(commerce: CommerceClient) -> None:
    order = await commerce.call("GET", "/orders/BM-24011", sandbox_id="test", customer_id="C-1001")
    assert order.data["shipment"]["delivered_at"] == (FIXED_NOW - timedelta(days=3)).strftime("%Y-%m-%d")
    track = await commerce.call("GET", "/orders/BM-24024/shipment", sandbox_id="test", customer_id="C-1002")
    assert track.data["days_since_last_event"] == 5.0


@pytest.mark.parametrize(
    ("fault", "status", "attempts"), [("timeout", "timeout", 2), ("error", "unavailable", 2), ("flaky", "ok", 2), ("slow", "timeout", 2)]
)
async def test_faults_and_retries(commerce_store: CommerceStore, commerce: CommerceClient, fault: str, status: str, attempts: int) -> None:
    commerce_store.sandbox("test").fault = fault  # type: ignore[assignment]
    outcome = await commerce.call("GET", "/orders/BM-24011", sandbox_id="test", customer_id="C-1001")
    assert (outcome.status, outcome.attempts) == (status, attempts)


async def test_writes_are_not_retried(commerce_store: CommerceStore, commerce: CommerceClient) -> None:
    commerce_store.sandbox("test").fault = "flaky"
    outcome = await commerce.call(
        "POST", "/orders/BM-24022/cancel", sandbox_id="test", customer_id="C-1002", json_body={"dry_run": False}, idempotent=False
    )
    assert (outcome.status, outcome.attempts) == ("unavailable", 1)
    assert commerce_store.sandbox("test").orders["BM-24022"]["status"] == "paid"


def test_sandboxes_are_isolated(commerce_store: CommerceStore) -> None:
    a, b = commerce_store.reset("a", fixed_now=FIXED_NOW), commerce_store.reset("b", fixed_now=FIXED_NOW)
    a.cancel(a.orders["BM-24022"], dry_run=False)
    assert a.orders["BM-24022"]["status"] == "cancelled"
    assert b.orders["BM-24022"]["status"] == "paid"
