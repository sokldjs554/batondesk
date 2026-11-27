"""FastAPI 앱 조립."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from batondesk import __version__
from batondesk.api.routers import conversations, handoffs, knowledge, ops
from batondesk.llm.base import LLMClient
from batondesk.obs.logging import configure_logging, log_event
from batondesk.service import ServiceError, SupportService
from batondesk.settings import Settings, get_settings
from batondesk.store.db import Database

DESCRIPTION = """
고객 상담 AI 에이전트 **BatonDesk** 의 API입니다.

- 주문·고객·정책은 가상의 예시 쇼핑몰 "바통마켓"의 **예시 데이터**입니다.
- `/example-commerce` 아래는 에이전트의 도구가 호출하는 **예시 주문 시스템**입니다(별도 문서: `/example-commerce/docs`).
- 데모 서버라 인증이 없습니다.
"""

class WebFiles(StaticFiles):
    """화면 파일을 내려 준다. 스크립트·스타일은 매번 서버에 새 것이 있는지 묻게(ETag 로 바뀌지 않았으면 304),
    글꼴 조각은 바뀌지 않으므로 오래 캐시한다. 캐시 지시가 없으면 브라우저가 옛 스타일시트를 계속 쓸 수 있다."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        response: Response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            response.headers["Cache-Control"] = "public, max-age=604800" if path.startswith("fonts/") else "no-cache"
        return response


PAGES = {
    "/": "index.html",
    "/chat": "chat.html",
    "/desk": "desk.html",
    "/ops": "ops.html",
    "/embed-demo": "embed-demo.html",
    "/review": "review.html",  # 사람 검수: 평가용 문항을 정답 없이 보여 주고 사람이 판정한다(결과는 브라우저에만 남는다)
}


def create_app(settings: Settings | None = None, *, llm: LLMClient | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service = SupportService(settings, llm=llm, db=db)
        recordings = settings.data_dir / "recordings"
        loaded = service.load_recordings(recordings) if settings.load_recordings and recordings.exists() else 0
        app.state.service = service
        app.mount("/example-commerce", service.commerce_app)
        if settings.web_dir.exists():
            app.mount("/static", WebFiles(directory=settings.web_dir), name="static")
        log_event("app.started", engine=service.engine, git_sha=settings.resolved_git_sha, recordings=loaded)
        try:
            yield
        finally:
            await service.aclose()

    app = FastAPI(title="BatonDesk API", version=__version__, description=DESCRIPTION, lifespan=lifespan)

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    app.include_router(conversations.router)
    app.include_router(handoffs.router)
    app.include_router(knowledge.router)
    app.include_router(ops.router)
    app.include_router(ops.demo)
    app.include_router(ops.channels)

    def page(filename: str):  # type: ignore[no-untyped-def]
        async def serve() -> FileResponse:
            return FileResponse(settings.web_dir / filename, headers={"Cache-Control": "no-cache"})

        return serve

    for route, filename in PAGES.items():
        app.add_api_route(route, page(filename), include_in_schema=False)

    return app


def app_factory() -> FastAPI:
    return create_app()
