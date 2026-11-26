"""예시 주문 시스템의 취소·반품 판정 규칙.

kb/ 의 정책 문서와 같은 숫자를 쓴다. 에이전트는 정책 설명은 지식 문서에서,
"이 주문에 적용하면 어떻게 되는가"는 이 판정 결과에서 가져온다.
tests/test_policy_consistency.py 가 두 곳의 숫자가 어긋나지 않는지 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

ReturnReason = Literal["change_of_mind", "defective", "wrong_item", "damaged_in_transit"]

RETURN_WINDOW_DAYS = 7
FAULT_RETURN_WINDOW_DAYS = 90  # "받은 날부터 3개월 이내"
FRESH_CLAIM_WINDOW_HOURS = 48
ELECTRONICS_DOA_DAYS = 14
RETURN_FEE_ONE_WAY = 3000
RETURN_FEE_ROUND_TRIP = 6000
BASE_SHIPPING_FEE = 3000
FREE_SHIPPING_THRESHOLD = 50000

CANCELLABLE_STATUSES = ("awaiting_payment", "paid", "preparing")
FAULT_REASONS = ("defective", "wrong_item", "damaged_in_transit")


@dataclass
class Decision:
    allowed: bool
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "code": self.code, "message": self.message, **self.details}


def items_total(order: dict[str, Any]) -> int:
    return sum(int(i["unit_price"]) * int(i["qty"]) for i in order["items"])


def paid_total(order: dict[str, Any]) -> int:
    return (
        items_total(order)
        + int(order.get("shipping_fee", 0))
        + int(order.get("remote_fee", 0))
        - int(order.get("discount", 0))
    )


def decide_cancel(order: dict[str, Any]) -> Decision:
    status = order["status"]
    if status == "cancelled":
        return Decision(False, "already_cancelled", "이미 취소된 주문입니다.")
    if status not in CANCELLABLE_STATUSES:
        return Decision(
            False,
            "already_shipped",
            "배송이 시작된 뒤에는 취소할 수 없습니다. 상품을 받은 뒤 반품으로 진행해야 합니다.",
            {"status": status},
        )
    if any(i.get("category") == "custom" and i.get("production_started") for i in order["items"]):
        return Decision(
            False, "custom_in_production", "주문제작 상품은 제작을 시작한 뒤에는 취소할 수 없습니다."
        )
    refund = 0 if status == "awaiting_payment" else paid_total(order)
    return Decision(
        True,
        "cancellable",
        "취소할 수 있습니다. 결제 금액 전액을 환불합니다.",
        {"refund_amount": refund, "payment_method": order["payment_method"]},
    )


def decide_return(
    order: dict[str, Any], item_id: str, reason: ReturnReason, now: datetime, delivered_at: datetime | None
) -> Decision:
    item = next((i for i in order["items"] if i["item_id"] == item_id), None)
    if item is None:
        return Decision(False, "item_not_found", "이 주문에 해당 상품이 없습니다.")
    if order["status"] == "return_requested":
        return Decision(False, "return_in_progress", "이미 반품이 접수되어 진행 중입니다.")
    if order["status"] != "delivered" or delivered_at is None:
        return Decision(
            False,
            "not_delivered",
            "아직 배송이 완료되지 않아 반품을 접수할 수 없습니다.",
            {"status": order["status"]},
        )

    category = item.get("category", "general")
    item_amount = int(item["unit_price"]) * int(item["qty"])
    fault = reason in FAULT_REASONS
    # "받은 날은 세지 않는다": 받은 날 다음 날 0시부터 센 N일째의 끝까지.
    start = (delivered_at + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    window_days = FAULT_RETURN_WINDOW_DAYS if fault else RETURN_WINDOW_DAYS
    deadline = start + timedelta(days=window_days) - timedelta(seconds=1)
    base = {
        "item_name": item["name"],
        "category": category,
        "reason": reason,
        "delivered_date": delivered_at.date().isoformat(),
        "deadline_date": deadline.date().isoformat(),
    }

    if category == "fresh":
        hours = (now - delivered_at).total_seconds() / 3600
        if not fault:
            return Decision(False, "fresh_no_change_of_mind", "신선식품은 단순변심으로 반품할 수 없습니다.", base)
        if hours > FRESH_CLAIM_WINDOW_HOURS:
            return Decision(
                False,
                "fresh_claim_window_passed",
                "신선식품 품질 문제는 받은 뒤 48시간 이내에만 접수할 수 있습니다.",
                base,
            )
        return Decision(
            True,
            "fresh_quality_claim",
            "48시간 이내라 품질 문제로 접수할 수 있습니다. 일부만 상한 경우의 부분 환불 금액은 상담원이 사진을 확인한 뒤 결정합니다.",
            {**base, "needs_agent_review": True, "return_fee": 0, "refund_amount": None},
        )

    if now > deadline:
        code = "fault_window_passed" if fault else "return_window_passed"
        return Decision(False, code, "반품 신청 기간이 지났습니다.", base)

    if not fault:
        if category == "custom":
            return Decision(False, "custom_made", "주문제작 상품은 단순변심으로 반품할 수 없습니다.", base)
        if category == "cosmetic" and item.get("seal_removed"):
            return Decision(False, "seal_removed", "밀봉 스티커를 뗀 화장품은 반품할 수 없습니다.", base)
        if category == "furniture":
            if item.get("assembled"):
                return Decision(False, "furniture_assembled", "조립·설치를 마친 가구는 단순변심 반품이 안 됩니다.", base)
            return Decision(
                True,
                "furniture_fee_unconfirmed",
                "조립 전이라 반품할 수 있습니다. 다만 가구 회수비는 시스템에 확정 금액이 없어 상담원 확인이 필요합니다.",
                {**base, "needs_agent_review": True, "return_fee": None, "refund_amount": None},
            )
        fee = RETURN_FEE_ONE_WAY if int(order.get("shipping_fee", 0)) > 0 else RETURN_FEE_ROUND_TRIP
        fee += int(order.get("remote_fee", 0))
        return Decision(
            True,
            "returnable_change_of_mind",
            "반품할 수 있습니다. 반품 배송비를 뺀 금액을 환불합니다.",
            {**base, "return_fee": fee, "refund_amount": item_amount - fee},
        )

    refund = item_amount + int(order.get("shipping_fee", 0)) + int(order.get("remote_fee", 0))
    return Decision(
        True,
        "returnable_fault",
        "하자·오배송 사유라 반품 배송비 없이 반품할 수 있습니다. 처음에 낸 배송비도 함께 환불합니다.",
        {**base, "return_fee": 0, "refund_amount": refund, "photo_required": True},
    )
