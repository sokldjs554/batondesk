"""LLM 호출 인터페이스.

에이전트·판정기는 이 인터페이스만 본다. 실제 구현은 Claude API(anthropic_llm.py)이고,
테스트는 미리 정해 둔 응답을 돌려주는 대역(scripted.py)을 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from batondesk.llm.pricing import estimate_usd


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class ToolCall:
    id: str
    name: str
    input: Any


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    model: str  # 응답이 알려 준 실제 모델
    usage: Usage
    latency_ms: float  # 마지막(성공한) 시도만 잰 값
    attempts: int = 1
    request_id: str | None = None
    refusal_category: str | None = None
    raw_content: Any = None  # 같은 턴의 다음 요청에 그대로 되돌려 보낼 원본 content
    engine: str = "anthropic"

    @property
    def cost_usd(self) -> float | None:
        return estimate_usd(
            self.model,
            self.usage.input_tokens,
            self.usage.output_tokens,
            self.usage.cache_read_input_tokens,
            self.usage.cache_creation_input_tokens,
        )

    def trace(self, purpose: str) -> dict[str, Any]:
        return {
            "type": "llm",
            "purpose": purpose,
            "engine": self.engine,
            "model": self.model,
            "stop_reason": self.stop_reason,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "usage": self.usage.as_dict(),
            "cost_usd": self.cost_usd,
            "request_id": self.request_id,
            "tool_calls": [{"name": t.name, "input": t.input} for t in self.tool_calls],
        }


class LLMError(Exception):
    """재시도까지 실패했거나 재시도할 수 없는 모델 호출 오류."""

    def __init__(self, message: str, *, kind: str, attempts: int = 1) -> None:
        super().__init__(message)
        self.kind = kind  # rate_limit | server | connection | bad_request | auth | timeout | budget
        self.attempts = attempts


@dataclass
class LLMRequest:
    purpose: str  # agent | grounding | resolution
    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    max_tokens: int = 4000
    effort: str = "low"


class LLMClient(Protocol):
    engine: str

    async def complete(self, request: LLMRequest) -> LLMResponse: ...
