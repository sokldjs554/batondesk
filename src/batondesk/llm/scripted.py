"""LLM이 아닌 대역(test double) 두 가지.

QueueLLM: 테스트가 미리 넣어 둔 응답을 순서대로 돌려준다. 에이전트 루프·인계·근거 검사 같은
파이프라인 배선을 모델 없이 결정적으로 검증하는 데 쓴다.

RuleLLM: 아주 단순한 규칙으로 응답을 만든다. 키 없이 화면을 개발·테스트할 때 쓰며,
이 모드에서는 화면과 API 응답에 "LLM 아님(규칙 엔진)"이 표시된다.

둘 다 답변 품질을 보여 주지 않는다. 품질 수치는 실제 모델로 돌린 평가에서만 나온다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from batondesk.llm.base import LLMRequest, LLMResponse, ToolCall, Usage

Responder = Callable[[LLMRequest], LLMResponse]


def fake_response(
    *,
    text: str = "",
    tool_calls: list[tuple[str, dict[str, Any]]] | None = None,
    stop_reason: str | None = None,
    model: str = "scripted",
    engine: str = "scripted",
) -> LLMResponse:
    calls = [ToolCall(id=f"toolu_fake_{i}", name=n, input=a) for i, (n, a) in enumerate(tool_calls or [], start=1)]
    raw: list[dict[str, Any]] = []
    if text:
        raw.append({"type": "text", "text": text})
    raw.extend({"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls)
    return LLMResponse(
        text=text,
        tool_calls=calls,
        stop_reason=stop_reason or ("tool_use" if calls else "end_turn"),
        model=model,
        usage=Usage(),
        latency_ms=0.0,
        raw_content=raw,
        engine=engine,
    )


def fake_final(payload: dict[str, Any], **kwargs: Any) -> LLMResponse:
    return fake_response(text=json.dumps(payload, ensure_ascii=False), **kwargs)


def agent_payload(
    message: str,
    *,
    action: str = "answer",
    citations: list[str] | None = None,
    handoff_reason: str = "none",
    handoff_summary: str = "",
    kb_issue: str = "",
) -> dict[str, Any]:
    return {
        "action": action,
        "message": message,
        "citations": citations or [],
        "handoff_reason": handoff_reason,
        "handoff_summary": handoff_summary,
        "kb_issue": kb_issue,
    }


class QueueLLM:
    engine = "scripted"

    def __init__(self, responses: list[LLMResponse | Responder] | None = None) -> None:
        self.queue: list[LLMResponse | Responder] = list(responses or [])
        self.requests: list[LLMRequest] = []

    def push(self, *responses: LLMResponse | Responder) -> None:
        self.queue.extend(responses)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self.queue:
            raise AssertionError(f"QueueLLM: 준비된 응답이 없습니다 (purpose={request.purpose})")
        item = self.queue.pop(0)
        response = item(request) if callable(item) else item
        response.model = request.model
        return response


_ORDER_RE = re.compile(r"BM-\d{5}", re.I)
_CUSTOMER_RE = re.compile(r"<고객_메시지>\n(.*?)\n</고객_메시지>", re.S)
_PASSAGE_RE = re.compile(r"^\[(K\d+)\] (.+)\n(.+)$", re.M)


def _last_user_blocks(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    content = messages[-1]["content"]
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content if isinstance(b, dict)]


class RuleLLM:
    """규칙 엔진. LLM이 아니다."""

    engine = "rule-engine"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        if request.purpose == "grounding":
            return fake_final({"claims": []}, model=request.model, engine=self.engine)
        if request.purpose == "resolution":
            return self._resolution(request)
        return self._agent(request)

    def _resolution(self, request: LLMRequest) -> LLMResponse:
        text = str(request.messages[-1]["content"])
        customer_lines = [ln for ln in text.splitlines() if ln.startswith("고객:")]
        last = customer_lines[-1] if customer_lines else ""
        if re.search(r"감사|고마|해결|알겠", last):
            status, signal = "resolved_confirmed", "positive"
        elif re.search(r"아직|안 ?돼|아니|불만|화나", last):
            status, signal = "unresolved", "negative"
        else:
            status, signal = "resolved_assumed", "none"
        payload = {"status": status, "customer_signal": signal, "reason": "규칙 엔진의 키워드 판정(LLM 아님)"}
        return fake_final(payload, model=request.model, engine=self.engine)

    def _agent(self, request: LLMRequest) -> LLMResponse:
        blocks = _last_user_blocks(request.messages)
        tool_results = [b for b in blocks if b.get("type") == "tool_result"]
        if tool_results:
            return self._after_tools(request, tool_results)
        joined = "\n".join(str(b.get("text", "")) for b in blocks if b.get("type") == "text")
        match = _CUSTOMER_RE.search(joined)
        customer = match.group(1) if match else joined
        if "상담원" in customer or "사람" in customer:
            return fake_final(
                agent_payload(
                    "상담원에게 연결해 드리겠습니다. 지금까지의 대화 내용을 함께 전달합니다.",
                    action="handoff",
                    handoff_reason="customer_request",
                    handoff_summary=f"고객이 상담원 연결을 요청했습니다. 마지막 메시지: {customer[:120]}",
                ),
                model=request.model,
                engine=self.engine,
            )
        order = _ORDER_RE.search(customer)
        if order:
            name = "track_shipment" if re.search(r"배송|어디|언제 ?와|도착", customer) else "get_order"
            return fake_response(tool_calls=[(name, {"order_id": order.group(0).upper()})], model=request.model, engine=self.engine)
        if re.search(r"주문|배송 ?조회|내 ?택배", customer) and not _PASSAGE_RE.search(joined):
            return fake_response(tool_calls=[("list_my_orders", {})], model=request.model, engine=self.engine)
        passage = _PASSAGE_RE.search(joined)
        if passage:
            body = passage.group(3).strip()
            sentences = re.split(r"(?<=[.다요])\s+", body)
            answer = " ".join(sentences[:2]).strip()
            return fake_final(agent_payload(answer, citations=[passage.group(1)]), model=request.model, engine=self.engine)
        return fake_final(
            agent_payload(
                "죄송합니다. 이 문의는 제가 가진 안내 문서에서 답을 찾지 못했습니다. 상담원에게 연결해 드리겠습니다.",
                action="handoff",
                handoff_reason="no_knowledge",
                handoff_summary=f"지식 문서에서 답을 찾지 못한 문의: {customer[:120]}",
            ),
            model=request.model,
            engine=self.engine,
        )

    def _after_tools(self, request: LLMRequest, tool_results: list[dict[str, Any]]) -> LLMResponse:
        parsed = []
        for block in tool_results:
            try:
                parsed.append((json.loads(str(block.get("content", "{}"))), bool(block.get("is_error"))))
            except ValueError:
                parsed.append(({}, True))
        if any(is_error for _, is_error in parsed):
            return fake_final(
                agent_payload(
                    "죄송합니다. 지금 주문 시스템에서 정보를 불러오지 못했습니다. 상담원에게 연결해 드리겠습니다.",
                    action="handoff",
                    handoff_reason="tool_failure",
                    handoff_summary="주문 시스템 조회가 재시도 후에도 실패했습니다.",
                ),
                model=request.model,
                engine=self.engine,
            )
        data, _ = parsed[0]
        evidence_id = data.get("evidence_id", "")
        result = data.get("result", {})
        if result.get("found") is False:
            message = "이 계정에서 해당 주문을 찾지 못했습니다. 주문번호를 다시 확인해 주시겠어요?"
            return fake_final(agent_payload(message, action="ask", citations=[evidence_id]), model=request.model, engine=self.engine)
        if "orders" in result:
            listing = ", ".join(f"{o['order_id']}({o['status_label']})" for o in result["orders"][:5])
            message = f"최근 주문은 다음과 같습니다: {listing}. 어느 주문에 대해 도와드릴까요?"
            return fake_final(agent_payload(message, action="ask", citations=[evidence_id]), model=request.model, engine=self.engine)
        if "events" in result:
            last = result["events"][-1] if result["events"] else None
            where = f"{last['location']}에서 {last['status']} 상태입니다" if last else "아직 출고 전입니다"
            message = f"주문 {result.get('order_id')}은(는) 현재 {result.get('status_label')}이며, {where}."
        else:
            message = f"주문 {result.get('order_id')}은(는) 현재 {result.get('status_label')} 상태입니다."
        return fake_final(agent_payload(message, citations=[evidence_id]), model=request.model, engine=self.engine)
