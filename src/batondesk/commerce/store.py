"""예시 주문 시스템의 저장소.

실제 주문 시스템이 아니다. data/commerce/orders.json 의 가상 주문을 메모리에 올려 두고,
취소·반품 접수 같은 쓰기 동작도 메모리 안에서만 바뀐다.

샌드박스: 공개 데모는 "demo" 샌드박스 하나를 쓰고, 평가는 문항·반복마다 새 샌드박스를
만들어 앞선 실행이 남긴 상태(취소된 주문 등)가 다음 문항의 채점에 섞이지 않게 한다.
"""

from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from batondesk.clock import now_kst
from batondesk.commerce import policy

FaultMode = Literal["none", "timeout", "error", "flaky", "slow"]
FAULT_MODES: tuple[str, ...] = ("none", "timeout", "error", "flaky", "slow")

STATUS_LABEL = {
    "awaiting_payment": "입금대기",
    "paid": "결제완료",
    "preparing": "상품준비중",
    "shipping": "배송중",
    "delivered": "배송완료",
    "cancelled": "취소완료",
    "return_requested": "반품접수",
}
PAYMENT_LABEL = {
    "card": "신용카드",
    "easy_pay": "간편결제",
    "bank_transfer": "실시간 계좌이체",
    "virtual_account": "무통장입금",
}
MAX_SANDBOXES = 300


class Sandbox:
    def __init__(self, seed: dict[str, Any], fixed_now: datetime | None = None) -> None:
        self.customers: dict[str, dict[str, Any]] = {c["customer_id"]: copy.deepcopy(c) for c in seed["customers"]}
        self.orders: dict[str, dict[str, Any]] = {o["order_id"]: copy.deepcopy(o) for o in seed["orders"]}
        self.fixed_now = fixed_now
        self.fault: FaultMode = "none"
        self.flaky_failures_left = 1
        self.audit: list[dict[str, Any]] = []
        self.lock = threading.RLock()

    def now(self) -> datetime:
        return self.fixed_now or now_kst()

    def _at(self, days_ago: float | None) -> datetime | None:
        if days_ago is None:
            return None
        return self.now() - timedelta(days=float(days_ago))

    # ---- 조회 ----------------------------------------------------------------

    def delivered_at(self, order: dict[str, Any]) -> datetime | None:
        shipment = order.get("shipment")
        return self._at(shipment.get("delivered_days_ago")) if shipment else None

    def view(self, order: dict[str, Any]) -> dict[str, Any]:
        """날짜를 기준 시각에 맞춰 풀어낸, 바깥에 보여 주는 주문 모습."""
        placed = self._at(order["placed_days_ago"])
        assert placed is not None
        shipment = order.get("shipment")
        out: dict[str, Any] = {
            "order_id": order["order_id"],
            "status": order["status"],
            "status_label": STATUS_LABEL[order["status"]],
            "ordered_at": placed.strftime("%Y-%m-%d %H:%M"),
            "payment_method": PAYMENT_LABEL[order["payment_method"]],
            "items": [
                {
                    "item_id": i["item_id"],
                    "name": i["name"],
                    "category": i.get("category", "general"),
                    "qty": i["qty"],
                    "unit_price": i["unit_price"],
                }
                for i in order["items"]
            ],
            "items_total": policy.items_total(order),
            "shipping_fee": order.get("shipping_fee", 0),
            "remote_area_fee": order.get("remote_fee", 0),
            "discount": order.get("discount", 0),
            "paid_total": policy.paid_total(order),
        }
        if order["status"] == "awaiting_payment":
            out["payment_due_at"] = (placed + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
        if shipment:
            shipped = self._at(shipment.get("shipped_days_ago"))
            delivered = self._at(shipment.get("delivered_days_ago"))
            out["shipment"] = {
                "carrier": shipment["carrier"],
                "tracking_no": shipment["tracking_no"],
                "shipped_at": shipped.strftime("%Y-%m-%d") if shipped else None,
                "delivered_at": delivered.strftime("%Y-%m-%d") if delivered else None,
            }
        if order.get("refund"):
            refund = order["refund"]
            processed = self._at(refund.get("processed_days_ago"))
            out["refund"] = {
                "amount": refund["amount"],
                "method": PAYMENT_LABEL.get(refund["method"], refund["method"]),
                "processed_at": processed.strftime("%Y-%m-%d") if processed else None,
                "reason": refund.get("reason"),
            }
        if order.get("return_request"):
            rr = order["return_request"]
            requested = self._at(rr.get("requested_days_ago"))
            out["return_request"] = {
                "item_id": rr["item_id"],
                "reason": rr["reason"],
                "requested_at": requested.strftime("%Y-%m-%d") if requested else None,
                "pickup_status": rr.get("pickup_status"),
                "pickup_expected_at": (
                    (self.now() + timedelta(days=float(rr["pickup_expected_in_days"]))).strftime("%Y-%m-%d")
                    if rr.get("pickup_expected_in_days") is not None
                    else None
                ),
            }
        return out

    def shipment_view(self, order: dict[str, Any]) -> dict[str, Any]:
        shipment = order.get("shipment")
        if not shipment:
            return {
                "order_id": order["order_id"],
                "status": order["status"],
                "status_label": STATUS_LABEL[order["status"]],
                "shipped": False,
                "events": [],
            }
        events = []
        for e in shipment["events"]:
            at = self._at(e["days_ago"])
            assert at is not None
            events.append({"at": at.strftime("%Y-%m-%d %H:%M"), "status": e["status"], "location": e["location"]})
        last = self._at(shipment["events"][-1]["days_ago"]) if shipment["events"] else None
        return {
            "order_id": order["order_id"],
            "status": order["status"],
            "status_label": STATUS_LABEL[order["status"]],
            "shipped": True,
            "carrier": shipment["carrier"],
            "tracking_no": shipment["tracking_no"],
            "events": events,
            "last_event_at": last.strftime("%Y-%m-%d %H:%M") if last else None,
            "days_since_last_event": round((self.now() - last).total_seconds() / 86400, 1) if last else None,
        }

    # ---- 쓰기 ----------------------------------------------------------------

    def cancel(self, order: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        with self.lock:
            decision = policy.decide_cancel(order)
            result = {**decision.as_dict(), "order_id": order["order_id"], "dry_run": dry_run}
            if "payment_method" in result:
                result["payment_method"] = PAYMENT_LABEL.get(result["payment_method"], result["payment_method"])
            if decision.allowed and not dry_run:
                refund = int(decision.details["refund_amount"])
                order["status"] = "cancelled"
                order["cancelled_days_ago"] = 0
                if refund:
                    order["refund"] = {
                        "amount": refund,
                        "processed_days_ago": None,
                        "method": order["payment_method"],
                        "reason": "고객 취소",
                    }
                self.audit.append({"action": "cancel", "order_id": order["order_id"], "refund": refund})
                result["executed"] = True
            return result

    def request_return(self, order: dict[str, Any], item_id: str, reason: str, dry_run: bool) -> dict[str, Any]:
        with self.lock:
            decision = policy.decide_return(order, item_id, reason, self.now(), self.delivered_at(order))  # type: ignore[arg-type]
            result = {**decision.as_dict(), "order_id": order["order_id"], "item_id": item_id, "dry_run": dry_run}
            if decision.allowed and not dry_run:
                if decision.details.get("needs_agent_review"):
                    # 금액을 시스템이 확정하지 못하는 건은 자동 접수하지 않는다.
                    result["executed"] = False
                    result["code"] = "needs_agent_review"
                    return result
                order["status"] = "return_requested"
                order["return_request"] = {
                    "item_id": item_id,
                    "reason": reason,
                    "requested_days_ago": 0,
                    "pickup_status": "회수 접수",
                    "pickup_expected_in_days": 2,
                }
                self.audit.append({"action": "return", "order_id": order["order_id"], "item_id": item_id, "reason": reason})
                result["executed"] = True
            return result

    def snapshot(self) -> dict[str, Any]:
        """채점용: 주문별 현재 상태."""
        return {oid: {"status": o["status"], "has_return_request": bool(o.get("return_request"))} for oid, o in self.orders.items()}


class CommerceStore:
    def __init__(self, seed_path: Path) -> None:
        self._seed = json.loads(seed_path.read_text(encoding="utf-8"))
        self._sandboxes: OrderedDict[str, Sandbox] = OrderedDict()
        self._lock = threading.Lock()

    def sandbox(self, sandbox_id: str = "demo") -> Sandbox:
        with self._lock:
            sb = self._sandboxes.get(sandbox_id)
            if sb is None:
                sb = Sandbox(self._seed)
                self._sandboxes[sandbox_id] = sb
                while len(self._sandboxes) > MAX_SANDBOXES:
                    oldest = next(iter(self._sandboxes))
                    if oldest == "demo":
                        self._sandboxes.move_to_end("demo")
                        continue
                    self._sandboxes.pop(oldest)
            else:
                self._sandboxes.move_to_end(sandbox_id)
            return sb

    def reset(self, sandbox_id: str = "demo", fixed_now: datetime | None = None) -> Sandbox:
        with self._lock:
            sb = Sandbox(self._seed, fixed_now=fixed_now)
            self._sandboxes[sandbox_id] = sb
            return sb

    def drop(self, sandbox_id: str) -> None:
        with self._lock:
            if sandbox_id != "demo":
                self._sandboxes.pop(sandbox_id, None)

    @property
    def customers(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self._seed["customers"]]
