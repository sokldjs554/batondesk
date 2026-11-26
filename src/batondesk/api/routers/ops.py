"""운영 화면이 쓰는 API: 지표, 실행 기록, 프롬프트 버전, 배포 정보, 데모 제어."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from batondesk import __version__
from batondesk.api.deps import get_service
from batondesk.api.schemas import FaultSet, WebhookMessage
from batondesk.clock import parse_now
from batondesk.obs.metrics import compute_metrics
from batondesk.service import ServiceError, SupportService

router = APIRouter(tags=["운영"])
_STARTED = time.time()


@router.get("/healthz", summary="상태 확인", tags=["운영"])
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/release", summary="배포된 코드·프롬프트·모델 정보")
async def release(service: SupportService = Depends(get_service)) -> dict[str, Any]:
    settings = service.settings
    in_use = {
        "agent": service.prompts.get("agent", settings.agent_prompt),
        "grounding": service.prompts.get("grounding", settings.grounding_prompt),
        "resolution": service.prompts.get("resolution", settings.resolution_prompt),
    }
    return {
        "name": "batondesk",
        "version": __version__,
        "git_sha": settings.resolved_git_sha,
        "uptime_s": round(time.time() - _STARTED),
        "engine": service.engine_info(),
        "prompts": {kind: {"id": version.id, "sha256": version.sha256[:12]} for kind, version in in_use.items()},
        "knowledge_base": {"documents": len(service.kb.docs), "chunks": len(service.kb.chunks)},
        "grounding_mode": settings.grounding_mode,
        "data_notice": "주문·고객·정책은 가상의 예시 쇼핑몰 '바통마켓'의 예시 데이터입니다.",
    }


@router.get("/v1/prompts", summary="프롬프트 버전 목록")
async def prompts(service: SupportService = Depends(get_service)) -> dict[str, Any]:
    lock = service.prompts.read_lock()
    return {
        "active": service.prompts.active,
        "versions": [{**v.describe(), "locked": v.id in lock} for v in service.prompts.all_versions()],
    }


@router.get("/v1/prompts/{version_id}", summary="프롬프트 버전 본문")
async def prompt_detail(version_id: str, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    version = service.prompts.versions.get(version_id)
    if version is None:
        raise ServiceError(404, "prompt_not_found", "프롬프트 버전을 찾을 수 없습니다.")
    return {**version.describe(), "system": version.system, "tool_descriptions": version.tool_descriptions}


@router.get("/v1/metrics/summary", summary="운영 지표")
async def metrics_summary(
    source: str | None = Query(default=None, description="live | recorded | rule-engine. 비우면 전체"),
    service: SupportService = Depends(get_service),
) -> dict[str, Any]:
    return {**compute_metrics(service.repo, source), "engine": service.engine_info()}


@router.get("/v1/events", summary="최근 운영 이벤트(로그)")
async def events(
    type: str | None = Query(default=None, description="llm.call | tool.call | kb.search | handoff.created | turn.completed …"),
    limit: int = Query(default=100, ge=1, le=500),
    service: SupportService = Depends(get_service),
) -> dict[str, Any]:
    return {"events": service.repo.events(type, limit)}


# ---- 데모 제어 -------------------------------------------------------------------

demo = APIRouter(prefix="/v1/demo", tags=["데모 제어"])


@demo.get("/customers", summary="예시 고객과 주문")
async def demo_customers(service: SupportService = Depends(get_service)) -> dict[str, Any]:
    sandbox = service.commerce_store.sandbox("demo")
    out = []
    for customer in service.customers():
        orders = [sandbox.view(o) for o in sandbox.orders.values() if o["customer_id"] == customer["customer_id"]]
        out.append(
            {
                **customer,
                "orders": [
                    {
                        "order_id": o["order_id"],
                        "status_label": o["status_label"],
                        "items": ", ".join(i["name"] for i in o["items"]),
                        "paid_total": o["paid_total"],
                        "ordered_at": o["ordered_at"],
                    }
                    for o in orders
                ],
            }
        )
    return {"customers": out, "notice": "가상의 예시 고객·주문입니다."}


@demo.get("/sandbox", summary="예시 주문 시스템 상태")
async def demo_sandbox(service: SupportService = Depends(get_service)) -> dict[str, Any]:
    sandbox = service.commerce_store.sandbox("demo")
    return {"fault": sandbox.fault, "now": sandbox.now().isoformat(timespec="seconds"), "audit": list(sandbox.audit)}


@demo.put("/fault", summary="예시 주문 시스템 장애 재현 켜기/끄기")
async def demo_fault(body: FaultSet, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    sandbox = service.commerce_store.sandbox("demo")
    with sandbox.lock:
        sandbox.fault = body.mode
        sandbox.flaky_failures_left = 1
    service.repo.add_event("demo.fault_set", None, {"mode": body.mode})
    return {"fault": sandbox.fault}


@demo.post("/reset", summary="예시 주문 데이터 초기화")
async def demo_reset(request: Request, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    fixed = parse_now(request.query_params.get("fixed_now"))
    service.commerce_store.reset("demo", fixed_now=fixed)
    return {"reset": True}


# ---- 채널 연동 -------------------------------------------------------------------

channels = APIRouter(prefix="/v1/channels", tags=["채널 연동"])


@channels.post(
    "/webhook",
    summary="외부 채팅 채널용 웹훅",
    description="메신저·채팅 플랫폼이 고객 메시지를 이 주소로 보내면 답변을 JSON으로 돌려준다. "
    "같은 (channel, external_user_key) 의 메시지는 같은 대화로 이어진다. 특정 플랫폼의 서명 검증은 넣지 않았다.",
)
async def webhook(body: WebhookMessage, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    key = f"{body.channel}:{body.external_user_key}"
    conv = service.repo.find_conversation_by_meta(body.channel, key)
    if conv is None:
        view = service.create_conversation(body.customer_id, channel=body.channel, meta={"external_key": key})
        conversation_id = view["id"]
    else:
        conversation_id = conv["id"]
    result = await service.handle_customer_message(conversation_id, body.text, client_key=key)
    if result["standby"]:
        return {"conversation_id": conversation_id, "reply": None, "action": "standby", "notice": result["notice"]}
    message = result["message"]
    return {
        "conversation_id": conversation_id,
        "reply": message["text"],
        "action": message["payload"]["action"],
        "sources": [{"id": c["id"], "title": c["title"]} for c in message["payload"]["citations"]],
        "handoff": bool(result["handoff"]),
    }
