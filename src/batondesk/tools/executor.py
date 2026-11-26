"""도구 실행기.

모델이 요청한 도구 호출을 검증하고 실행한 뒤, 모델에게 돌려줄 결과와 실행 기록을 만든다.

안전장치(프롬프트가 아니라 코드로 강제하는 것):
1. 허용 목록 — 정의되지 않은 도구 이름은 실행하지 않는다.
2. 고객 범위 — 주문 조회에는 대화에 묶인 고객 ID만 실린다. 모델이 다른 고객을 지정할 방법이 없다.
3. 쓰기 전 고객 확인 — 취소·반품 접수는 "확인(check_*)으로 받은 코드"가 있어야 하고,
   그 코드는 발급된 턴에서는 쓸 수 없다. 즉 고객 메시지가 한 번 더 온 뒤에만 실행된다.
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from batondesk.agent.evidence import Evidence, EvidencePool
from batondesk.kb.retriever import KnowledgeBase
from batondesk.tools.client import CallOutcome, CommerceClient
from batondesk.tools.specs import RETURN_REASONS, TOOL_NAMES

_ORDER_ID_RE = re.compile(r"^BM-\d{5}$")


@dataclass
class PendingAction:
    code: str
    action: str  # "cancel" | "return"
    order_id: str
    item_id: str | None
    reason: str | None
    issued_turn: int
    used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ToolContext:
    customer_id: str
    sandbox_id: str
    turn_seq: int
    today: date
    pool: EvidencePool
    pending: dict[str, PendingAction] = field(default_factory=dict)
    retrieval_k: int = 5


@dataclass
class ToolResult:
    name: str
    args: dict[str, Any]
    content: str  # 모델에게 돌려주는 본문(JSON 문자열)
    is_error: bool
    status: str  # ok | not_found | rejected | unavailable | timeout | invalid | denied
    latency_ms: float
    attempts: int = 1
    evidence_ids: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def trace(self) -> dict[str, Any]:
        return {
            "type": "tool",
            "name": self.name,
            "args": self.args,
            "status": self.status,
            "is_error": self.is_error,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "evidence_ids": self.evidence_ids,
            **({"detail": self.detail} if self.detail else {}),
        }


_FAILURE_GUIDE = {
    "timeout": "주문 시스템이 제한 시간 안에 응답하지 않았습니다. 재시도까지 실패했습니다. 주문 정보를 추측해서 답하지 마세요.",
    "unavailable": "주문 시스템에 오류가 있어 조회하지 못했습니다. 재시도까지 실패했습니다. 주문 정보를 추측해서 답하지 마세요.",
}


class ToolExecutor:
    def __init__(self, kb: KnowledgeBase, commerce: CommerceClient) -> None:
        self.kb = kb
        self.commerce = commerce

    async def run(self, name: str, args: Any, ctx: ToolContext) -> ToolResult:
        started = time.perf_counter()
        if name not in TOOL_NAMES:
            return self._invalid(name, args, f"허용되지 않은 도구입니다: {name}", started, status="denied")
        if not isinstance(args, dict):
            return self._invalid(name, {}, "도구 인자는 객체여야 합니다.", started)
        handler = getattr(self, f"_tool_{name}")
        result: ToolResult = await handler(args, ctx, started)
        return result

    # ---- 지식 검색 ------------------------------------------------------------

    def search(self, query: str, ctx: ToolContext) -> list[Evidence]:
        hits = self.kb.search(query, k=ctx.retrieval_k)
        return [ctx.pool.add_chunk(h.chunk, ctx.today, ctx.turn_seq, query, h.score) for h in hits]

    async def _tool_search_knowledge(self, args: dict[str, Any], ctx: ToolContext, started: float) -> ToolResult:
        query = str(args.get("query", "")).strip()
        if not query:
            return self._invalid("search_knowledge", args, "query 가 비어 있습니다.", started)
        found = self.search(query, ctx)
        body = {
            "query": query,
            "passages": [{"evidence_id": e.id, "title": e.title, "text": e.text} for e in found],
            "note": "검색 결과에 답이 없으면 없다고 말하세요. 결과에 없는 내용을 지어내지 마세요."
            if found
            else "일치하는 문서가 없습니다.",
        }
        return ToolResult(
            "search_knowledge",
            {"query": query},
            json.dumps(body, ensure_ascii=False),
            False,
            "ok",
            _ms(started),
            evidence_ids=[e.id for e in found],
            detail={"hits": [{"evidence_id": e.id, "chunk_id": e.meta["chunk_id"], "score": e.meta["score"]} for e in found]},
        )

    # ---- 주문 조회 ------------------------------------------------------------

    async def _tool_list_my_orders(self, args: dict[str, Any], ctx: ToolContext, started: float) -> ToolResult:
        outcome = await self.commerce.call(
            "GET", f"/customers/{ctx.customer_id}/orders", sandbox_id=ctx.sandbox_id, customer_id=ctx.customer_id
        )
        return self._from_outcome("list_my_orders", {}, outcome, ctx, started)

    async def _tool_get_order(self, args: dict[str, Any], ctx: ToolContext, started: float) -> ToolResult:
        order_id = _norm_order_id(args.get("order_id"))
        if order_id is None:
            return self._invalid("get_order", args, "주문번호 형식이 아닙니다. 형식: BM-숫자5자리 (예: BM-24011)", started)
        outcome = await self.commerce.call("GET", f"/orders/{order_id}", sandbox_id=ctx.sandbox_id, customer_id=ctx.customer_id)
        return self._from_outcome("get_order", {"order_id": order_id}, outcome, ctx, started)

    async def _tool_track_shipment(self, args: dict[str, Any], ctx: ToolContext, started: float) -> ToolResult:
        order_id = _norm_order_id(args.get("order_id"))
        if order_id is None:
            return self._invalid("track_shipment", args, "주문번호 형식이 아닙니다. 형식: BM-숫자5자리", started)
        outcome = await self.commerce.call("GET", f"/orders/{order_id}/shipment", sandbox_id=ctx.sandbox_id, customer_id=ctx.customer_id)
        return self._from_outcome("track_shipment", {"order_id": order_id}, outcome, ctx, started)

    # ---- 취소·반품: 확인 → (다음 턴) 실행 ----------------------------------------

    async def _tool_check_cancellation(self, args: dict[str, Any], ctx: ToolContext, started: float) -> ToolResult:
        order_id = _norm_order_id(args.get("order_id"))
        if order_id is None:
            return self._invalid("check_cancellation", args, "주문번호 형식이 아닙니다. 형식: BM-숫자5자리", started)
        outcome = await self.commerce.call(
            "POST",
            f"/orders/{order_id}/cancel",
            sandbox_id=ctx.sandbox_id,
            customer_id=ctx.customer_id,
            json_body={"dry_run": True},
        )
        if outcome.ok and outcome.data.get("allowed"):
            outcome.data["confirmation_code"] = self._issue(ctx, "cancel", order_id, None, None)
            outcome.data["next_step"] = (
                "고객에게 환불 금액을 알리고 취소할지 물어보세요. 고객이 다음 메시지에서 동의하면 execute_confirmed_action 을 부르세요."
            )
        return self._from_outcome("check_cancellation", {"order_id": order_id}, outcome, ctx, started)

    async def _tool_check_return(self, args: dict[str, Any], ctx: ToolContext, started: float) -> ToolResult:
        order_id = _norm_order_id(args.get("order_id"))
        item_id = str(args.get("item_id", "")).strip()
        reason = str(args.get("reason", "")).strip()
        if order_id is None:
            return self._invalid("check_return", args, "주문번호 형식이 아닙니다. 형식: BM-숫자5자리", started)
        if reason not in RETURN_REASONS or not item_id:
            return self._invalid("check_return", args, f"item_id 와 reason({', '.join(RETURN_REASONS)})이 필요합니다.", started)
        clean = {"order_id": order_id, "item_id": item_id, "reason": reason}
        outcome = await self.commerce.call(
            "POST",
            f"/orders/{order_id}/returns",
            sandbox_id=ctx.sandbox_id,
            customer_id=ctx.customer_id,
            json_body={"item_id": item_id, "reason": reason, "dry_run": True},
        )
        if outcome.ok and outcome.data.get("allowed") and not outcome.data.get("needs_agent_review"):
            outcome.data["confirmation_code"] = self._issue(ctx, "return", order_id, item_id, reason)
            outcome.data["next_step"] = (
                "고객에게 반품 배송비와 환불 예상 금액을 알리고 접수할지 물어보세요. 고객이 다음 메시지에서 동의하면 execute_confirmed_action 을 부르세요."
            )
        return self._from_outcome("check_return", clean, outcome, ctx, started)

    async def _tool_execute_confirmed_action(self, args: dict[str, Any], ctx: ToolContext, started: float) -> ToolResult:
        code = str(args.get("confirmation_code", "")).strip()
        clean = {"confirmation_code": code}
        pending = ctx.pending.get(code)
        if pending is None or pending.used:
            return self._denied(
                "execute_confirmed_action",
                clean,
                "유효한 확인 코드가 아닙니다. check_cancellation 또는 check_return 으로 먼저 확인하세요.",
                started,
            )
        if pending.issued_turn >= ctx.turn_seq:
            return self._denied(
                "execute_confirmed_action",
                clean,
                "이 코드는 방금 발급되었습니다. 고객에게 내용을 안내하고 고객의 동의 메시지를 받은 다음 턴에 실행할 수 있습니다.",
                started,
            )
        body: dict[str, Any]
        if pending.action == "cancel":
            path, body = f"/orders/{pending.order_id}/cancel", {"dry_run": False}
        else:
            path = f"/orders/{pending.order_id}/returns"
            body = {"item_id": pending.item_id, "reason": pending.reason, "dry_run": False}
        outcome = await self.commerce.call(
            "POST", path, sandbox_id=ctx.sandbox_id, customer_id=ctx.customer_id, json_body=body, idempotent=False
        )
        if outcome.ok and outcome.data.get("executed"):
            pending.used = True
        return self._from_outcome(
            "execute_confirmed_action",
            {**clean, "action": pending.action, "order_id": pending.order_id},
            outcome,
            ctx,
            started,
        )

    # ---- 내부 ----------------------------------------------------------------

    def _issue(self, ctx: ToolContext, action: str, order_id: str, item_id: str | None, reason: str | None) -> str:
        code = f"CF-{secrets.token_hex(3).upper()}"
        ctx.pending[code] = PendingAction(code, action, order_id, item_id, reason, ctx.turn_seq)
        return code

    def _from_outcome(self, name: str, args: dict[str, Any], outcome: CallOutcome, ctx: ToolContext, started: float) -> ToolResult:
        if outcome.ok:
            ev = ctx.pool.add_tool_result(name, args, outcome.data, ctx.turn_seq, "ok")
            content = json.dumps({"evidence_id": ev.id, "result": outcome.data}, ensure_ascii=False)
            return ToolResult(name, args, content, False, "ok", _ms(started), outcome.attempts, [ev.id])
        if outcome.status == "not_found":
            # "없다"는 것도 답변의 근거가 된다(없는 주문을 있다고 말하면 안 되므로).
            data = {"found": False, "message": outcome.error or "찾을 수 없습니다."}
            ev = ctx.pool.add_tool_result(name, args, data, ctx.turn_seq, "not_found")
            content = json.dumps({"evidence_id": ev.id, "result": data}, ensure_ascii=False)
            return ToolResult(name, args, content, False, "not_found", _ms(started), outcome.attempts, [ev.id])
        guide = _FAILURE_GUIDE.get(outcome.status, outcome.error or "요청이 거부되었습니다.")
        content = json.dumps({"error": outcome.status, "message": guide, "attempts": outcome.attempts}, ensure_ascii=False)
        return ToolResult(name, args, content, True, outcome.status, _ms(started), outcome.attempts, detail={"error": outcome.error})

    def _invalid(self, name: str, args: Any, message: str, started: float, status: str = "invalid") -> ToolResult:
        content = json.dumps({"error": status, "message": message}, ensure_ascii=False)
        return ToolResult(name, args if isinstance(args, dict) else {}, content, True, status, _ms(started))

    def _denied(self, name: str, args: dict[str, Any], message: str, started: float) -> ToolResult:
        return self._invalid(name, args, message, started, status="denied")


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def _norm_order_id(value: Any) -> str | None:
    text = str(value or "").strip().upper().replace(" ", "")
    return text if _ORDER_ID_RE.match(text) else None
