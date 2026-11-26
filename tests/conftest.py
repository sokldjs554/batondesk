"""테스트 공용 준비물.

여기 테스트들은 실제 모델을 부르지 않는다. 정해 둔 응답을 돌려주는 대역(QueueLLM)으로
"파이프라인이 설계대로 이어지는가"만 본다. 답변 품질은 evals/ 의 실제 모델 평가가 본다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from batondesk.api.app import create_app
from batondesk.clock import KST
from batondesk.commerce.app import create_commerce_app
from batondesk.commerce.store import CommerceStore
from batondesk.kb.retriever import KnowledgeBase
from batondesk.llm.scripted import QueueLLM
from batondesk.prompts.registry import PromptRegistry
from batondesk.service import SupportService
from batondesk.settings import REPO_ROOT, Settings
from batondesk.store.db import Database
from batondesk.tools.client import CommerceClient
from batondesk.tools.executor import ToolExecutor

FIXED_NOW = datetime(2026, 11, 20, 10, 0, tzinfo=KST)
# 서비스가 쓰는 프롬프트 버전(prompts/manifest.yaml 의 active). 버전을 올려도 테스트를 고치지 않게 여기서 읽는다.
ACTIVE = PromptRegistry(REPO_ROOT / "prompts").active


@pytest.fixture(scope="session")
def kb() -> KnowledgeBase:
    return KnowledgeBase.from_dir(REPO_ROOT / "kb")


@pytest.fixture(scope="session")
def prompts() -> PromptRegistry:
    return PromptRegistry(REPO_ROOT / "prompts")


@pytest.fixture
def commerce_store() -> CommerceStore:
    store = CommerceStore(REPO_ROOT / "data" / "commerce" / "orders.json")
    store.reset("test", fixed_now=FIXED_NOW)
    return store


@pytest.fixture
async def commerce(commerce_store: CommerceStore) -> AsyncIterator[CommerceClient]:
    app = create_commerce_app(commerce_store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://commerce") as http:
        yield CommerceClient(http, timeout_s=0.4, retries=1)


@pytest.fixture
def executor(kb: KnowledgeBase, commerce: CommerceClient) -> ToolExecutor:
    return ToolExecutor(kb, commerce)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    # 저장된 데모 기록(data/recordings)은 싣지 않는다. 테스트가 세는 대화 수가 기록 파일 수에 따라 달라지면 안 된다.
    return Settings(db_path=tmp_path / "test.sqlite3", llm_mode="scripted", tool_timeout_s=0.4, load_recordings=False, _env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def llm() -> QueueLLM:
    return QueueLLM()


@pytest.fixture
async def service(settings: Settings, llm: QueueLLM) -> AsyncIterator[SupportService]:
    svc = SupportService(settings, llm=llm, db=Database(":memory:"))
    svc.resolution_inline = True
    svc.commerce_store.reset("demo", fixed_now=FIXED_NOW)
    yield svc
    await svc.aclose()


@pytest.fixture
def client(settings: Settings, llm: QueueLLM) -> Iterator[TestClient]:
    app = create_app(settings, llm=llm)
    with TestClient(app) as test_client:
        app.state.service.resolution_inline = True
        app.state.service.commerce_store.reset("demo", fixed_now=FIXED_NOW)
        yield test_client
