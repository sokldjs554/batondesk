"""실행 설정. 환경 변수 `BATONDESK_*` 로 바꾼다(.env.example 참고)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BATONDESK_", env_file=".env", extra="ignore")

    # 경로
    kb_dir: Path = REPO_ROOT / "kb"
    data_dir: Path = REPO_ROOT / "data"
    prompts_dir: Path = REPO_ROOT / "prompts"
    web_dir: Path = REPO_ROOT / "web"
    db_path: Path = REPO_ROOT / "var" / "batondesk.sqlite3"

    # LLM
    # auto: ANTHROPIC_API_KEY 가 있으면 live, 없으면 off(대화 API가 503을 돌려주고 화면은 저장 기록만 보여 준다).
    # scripted: LLM이 아닌 규칙 엔진. 테스트와 화면 개발용이며 화면에 그렇게 표시된다.
    llm_mode: Literal["auto", "live", "scripted", "off"] = "auto"
    agent_model: str = "claude-haiku-5-5"
    judge_model: str = "claude-haiku-5-5"
    agent_effort: Literal["low", "medium", "high"] = "low"
    judge_effort: Literal["low", "medium", "high"] = "low"
    agent_max_tokens: int = 4000
    judge_max_tokens: int = 3000
    llm_timeout_s: float = 60.0
    llm_max_attempts: int = 3

    # 프롬프트 버전(None 이면 prompts/manifest.yaml 의 active)
    agent_prompt: str | None = None
    grounding_prompt: str | None = None
    resolution_prompt: str | None = None

    # 에이전트
    retrieval_k: int = 5
    max_tool_rounds: int = 6
    grounding_mode: Literal["block", "flag", "off"] = "block"
    max_revisions: int = 1
    resolution_check: bool = True

    # 도구(예시 주문 시스템 호출)
    commerce_url: str | None = None  # None 이면 같은 프로세스의 예시 주문 API를 호출
    tool_timeout_s: float = 2.5
    tool_retries: int = 1

    # 공개 데모 보호
    daily_usd_cap: float = Field(default=1.5, description="하루 모델 호출 추정 비용 상한(USD). 넘으면 live 대화를 막는다.")
    max_customer_turns: int = 16
    max_message_chars: int = 600
    rate_limit_per_hour: int = 40

    git_sha: str | None = None

    @property
    def anthropic_api_key_present(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def effective_llm_mode(self) -> Literal["live", "scripted", "off"]:
        if self.llm_mode == "auto":
            return "live" if self.anthropic_api_key_present else "off"
        return self.llm_mode

    @property
    def resolved_git_sha(self) -> str:
        return self.git_sha or os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("GITHUB_SHA") or "unknown"


@lru_cache
def get_settings() -> Settings:
    return Settings()
