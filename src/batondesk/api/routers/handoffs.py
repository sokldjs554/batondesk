"""상담원 화면이 쓰는 인계 API. 데모라 인증이 없다(운영이라면 상담원 로그인이 붙을 자리)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from batondesk.api.deps import get_service
from batondesk.api.schemas import AgentAction, AgentReply, HandoffResolve
from batondesk.service import SupportService

router = APIRouter(prefix="/v1/handoffs", tags=["상담원 인계"])


@router.get("", summary="인계 목록")
async def list_handoffs(
    status: str | None = Query(default=None, description="waiting | assigned | resolved"),
    service: SupportService = Depends(get_service),
) -> dict[str, Any]:
    return {"handoffs": service.handoff_list(status)}


@router.get("/{handoff_id}", summary="인계 건 상세(대화·근거·도구 결과·미해결 사유)")
async def get_handoff(handoff_id: str, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    return service.handoff_view(handoff_id)


@router.post("/{handoff_id}/claim", summary="상담원이 맡기")
async def claim(handoff_id: str, body: AgentAction, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    return service.claim_handoff(handoff_id, body.agent_name)


@router.post("/{handoff_id}/reply", summary="상담원이 고객에게 답하기")
async def reply(handoff_id: str, body: AgentReply, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    return service.agent_reply(handoff_id, body.agent_name, body.text)


@router.post("/{handoff_id}/resolve", summary="인계 건 종료")
async def resolve(handoff_id: str, body: HandoffResolve, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    return service.resolve_handoff(handoff_id, body.agent_name, body.note, body.return_to_ai)
