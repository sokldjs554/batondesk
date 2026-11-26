from __future__ import annotations

from fastapi import Request

from batondesk.service import ServiceError, SupportService


def get_service(request: Request) -> SupportService:
    service: SupportService = request.app.state.service
    return service


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def guard_llm(service: SupportService, key: str) -> None:
    """모델을 부르는 공개 엔드포인트에 거는 한도 검사."""
    if service.engine != "live":
        return
    if service.repo.spend_today()["usd"] >= service.settings.daily_usd_cap:
        raise ServiceError(429, "budget_exhausted", "오늘의 데모 모델 호출 한도를 다 썼습니다.")
    if service.repo.hit_rate(key) > service.settings.rate_limit_per_hour:
        raise ServiceError(429, "rate_limited", "요청이 많습니다. 잠시 뒤에 다시 시도해 주세요.")
