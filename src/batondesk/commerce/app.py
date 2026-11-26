"""예시 주문 시스템 API (별도 ASGI 앱).

에이전트의 도구는 이 API를 HTTP로 호출한다. 실제 서비스라면 사내 주문 시스템이 있을 자리다.
모든 응답 헤더에 `X-Example-System: true` 를 붙여 예시 시스템임을 드러낸다.

장애 재현: 샌드박스마다 장애 모드를 켤 수 있다.
- timeout: 응답을 8초 미룬다(도구 클라이언트의 제한 시간보다 길다).
- error:   500을 돌려준다.
- flaky:   첫 호출만 500, 다음부터 정상(재시도 로직 확인용).
- slow:    1.2초 늦게 정상 응답한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from batondesk.clock import parse_now
from batondesk.commerce.policy import ReturnReason
from batondesk.commerce.store import FAULT_MODES, CommerceStore, FaultMode, Sandbox

TIMEOUT_FAULT_SECONDS = 8.0
SLOW_FAULT_SECONDS = 1.2


class ReturnRequest(BaseModel):
    item_id: str
    reason: ReturnReason
    dry_run: bool = True


class CancelRequest(BaseModel):
    dry_run: bool = True


class FaultRequest(BaseModel):
    mode: FaultMode


class ResetRequest(BaseModel):
    fixed_now: str | None = Field(default=None, description="기준 시각을 고정(ISO 8601). 평가에서 쓴다.")


def create_commerce_app(store: CommerceStore) -> FastAPI:
    app = FastAPI(
        title="바통마켓 예시 주문 시스템",
        description="가상의 예시 API입니다. 실제 주문·결제 시스템과 연결되어 있지 않습니다.",
        version="1.0.0",
    )

    @app.middleware("http")
    async def mark_example(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers["X-Example-System"] = "true"
        return response

    def get_sandbox(x_sandbox_id: str = Header(default="demo")) -> Sandbox:
        return store.sandbox(x_sandbox_id)

    async def apply_fault(sb: Sandbox = Depends(get_sandbox)) -> None:
        mode = sb.fault
        if mode == "timeout":
            await asyncio.sleep(TIMEOUT_FAULT_SECONDS)
            raise HTTPException(504, {"code": "upstream_timeout", "message": "주문 시스템 응답 지연(재현)"})
        if mode == "error":
            raise HTTPException(500, {"code": "internal_error", "message": "주문 시스템 오류(재현)"})
        if mode == "flaky":
            with sb.lock:
                if sb.flaky_failures_left > 0:
                    sb.flaky_failures_left -= 1
                    raise HTTPException(500, {"code": "internal_error", "message": "일시 오류(재현)"})
        if mode == "slow":
            await asyncio.sleep(SLOW_FAULT_SECONDS)

    def get_order(order_id: str, sb: Sandbox, customer_id: str) -> dict[str, Any]:
        order = sb.orders.get(order_id.strip().upper())
        # 다른 고객의 주문은 "없음"과 구분하지 않는다(주문번호 존재 여부를 흘리지 않는다).
        if order is None or order["customer_id"] != customer_id:
            raise HTTPException(404, {"code": "order_not_found", "message": "이 계정에서 해당 주문을 찾을 수 없습니다."})
        return order

    @app.get("/customers/{customer_id}/orders", dependencies=[Depends(apply_fault)])
    async def list_orders(customer_id: str, sb: Sandbox = Depends(get_sandbox)) -> dict[str, Any]:
        if customer_id not in sb.customers:
            raise HTTPException(404, {"code": "customer_not_found", "message": "고객을 찾을 수 없습니다."})
        orders = [o for o in sb.orders.values() if o["customer_id"] == customer_id]
        orders.sort(key=lambda o: o["placed_days_ago"])
        return {
            "customer_id": customer_id,
            "orders": [
                {
                    "order_id": o["order_id"],
                    "status": o["status"],
                    "status_label": sb.view(o)["status_label"],
                    "ordered_at": sb.view(o)["ordered_at"],
                    "item_names": [i["name"] for i in o["items"]],
                    "paid_total": sb.view(o)["paid_total"],
                }
                for o in orders
            ],
        }

    @app.get("/orders/{order_id}", dependencies=[Depends(apply_fault)])
    async def read_order(order_id: str, x_customer_id: str = Header(), sb: Sandbox = Depends(get_sandbox)) -> dict[str, Any]:
        return sb.view(get_order(order_id, sb, x_customer_id))

    @app.get("/orders/{order_id}/shipment", dependencies=[Depends(apply_fault)])
    async def read_shipment(order_id: str, x_customer_id: str = Header(), sb: Sandbox = Depends(get_sandbox)) -> dict[str, Any]:
        return sb.shipment_view(get_order(order_id, sb, x_customer_id))

    @app.post("/orders/{order_id}/cancel", dependencies=[Depends(apply_fault)])
    async def cancel_order(
        order_id: str, body: CancelRequest, x_customer_id: str = Header(), sb: Sandbox = Depends(get_sandbox)
    ) -> dict[str, Any]:
        return sb.cancel(get_order(order_id, sb, x_customer_id), dry_run=body.dry_run)

    @app.post("/orders/{order_id}/returns", dependencies=[Depends(apply_fault)])
    async def request_return(
        order_id: str, body: ReturnRequest, x_customer_id: str = Header(), sb: Sandbox = Depends(get_sandbox)
    ) -> dict[str, Any]:
        return sb.request_return(get_order(order_id, sb, x_customer_id), body.item_id, body.reason, body.dry_run)

    # ---- 데모·평가 제어 (장애 주입의 영향을 받지 않는다) -------------------------

    @app.get("/sandbox")
    async def read_sandbox(x_sandbox_id: str = Header(default="demo"), sb: Sandbox = Depends(get_sandbox)) -> dict[str, Any]:
        return {
            "sandbox_id": x_sandbox_id,
            "fault": sb.fault,
            "fault_modes": list(FAULT_MODES),
            "now": sb.now().isoformat(timespec="seconds"),
            "clock_fixed": sb.fixed_now is not None,
            "orders": sb.snapshot(),
            "audit": list(sb.audit),
        }

    @app.put("/sandbox/fault")
    async def set_fault(body: FaultRequest, sb: Sandbox = Depends(get_sandbox)) -> dict[str, Any]:
        with sb.lock:
            sb.fault = body.mode
            sb.flaky_failures_left = 1
        return {"fault": sb.fault}

    @app.post("/sandbox/reset")
    async def reset(body: ResetRequest | None = None, x_sandbox_id: str = Header(default="demo")) -> dict[str, Any]:
        fixed = parse_now(body.fixed_now) if body else None
        sb = store.reset(x_sandbox_id, fixed_now=fixed)
        return {"sandbox_id": x_sandbox_id, "now": sb.now().isoformat(timespec="seconds"), "reset": True}

    return app
