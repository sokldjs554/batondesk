"""고객 대화 API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from batondesk.api.deps import client_key, get_service
from batondesk.api.schemas import ConversationCreate, FeedbackCreate, MessageCreate
from batondesk.service import ServiceError, SupportService

router = APIRouter(prefix="/v1/conversations", tags=["대화"])


@router.post("", summary="대화 시작")
async def create_conversation(body: ConversationCreate, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    return service.create_conversation(body.customer_id, channel=body.channel)


@router.get("", summary="대화 목록")
async def list_conversations(
    status: str | None = Query(default=None, description="open | handed_off | closed"),
    source: str | None = Query(default=None, description="live | recorded | rule-engine"),
    limit: int = Query(default=50, ge=1, le=200),
    service: SupportService = Depends(get_service),
) -> dict[str, Any]:
    return {"conversations": service.conversation_list(status=status, source=source, limit=limit)}


@router.get("/{conversation_id}", summary="대화 조회")
async def get_conversation(
    conversation_id: str,
    after_seq: int = Query(default=0, ge=0, description="이 번호 뒤의 메시지만 받는다(새 메시지 확인용)"),
    trace: bool = Query(default=False, description="true 면 턴별 실행 기록과 근거를 함께 준다"),
    service: SupportService = Depends(get_service),
) -> dict[str, Any]:
    return service.conversation_view(conversation_id, after_seq=after_seq, with_trace=trace)


@router.post("/{conversation_id}/messages", summary="고객 메시지 보내기(응답을 한 번에 받음)")
async def post_message(
    conversation_id: str, body: MessageCreate, request: Request, service: SupportService = Depends(get_service)
) -> dict[str, Any]:
    return await service.handle_customer_message(conversation_id, body.text, client_key=client_key(request))


@router.post(
    "/{conversation_id}/messages/stream",
    summary="고객 메시지 보내기(진행 상황을 SSE로 받음)",
    description="이벤트: `progress`(단계 안내) → `result`(최종 응답) 또는 `error`. 답변 글자는 흘려보내지 않는다 — "
    "근거 검사를 통과한 답변만 한 번에 내보내기 때문이다.",
)
async def post_message_stream(
    conversation_id: str, body: MessageCreate, request: Request, service: SupportService = Depends(get_service)
) -> StreamingResponse:
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
    key = client_key(request)

    async def on_progress(event: dict[str, Any]) -> None:
        await queue.put(("progress", event))

    async def run() -> None:
        try:
            result = await service.handle_customer_message(conversation_id, body.text, on_progress=on_progress, client_key=key)
            await queue.put(("result", result))
        except ServiceError as exc:
            await queue.put(("error", {"code": exc.code, "message": exc.message, "status": exc.status_code}))
        except Exception:
            await queue.put(("error", {"code": "internal_error", "message": "처리 중 오류가 발생했습니다.", "status": 500}))
            raise
        finally:
            await queue.put(None)

    async def stream() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, data = item
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        finally:
            # 연결이 끊겨도 턴은 끝까지 처리해 기록을 남긴다.
            if not task.done():
                await asyncio.shield(task)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{conversation_id}/handoff", summary="고객이 상담원 연결을 요청")
async def request_handoff(conversation_id: str, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    return await service.request_handoff(conversation_id)


@router.post("/{conversation_id}/feedback", summary="해결 여부 피드백")
async def feedback(conversation_id: str, body: FeedbackCreate, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    return await service.feedback(conversation_id, body.resolved)
