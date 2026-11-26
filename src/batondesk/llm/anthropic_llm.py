"""Claude API 호출부.

- 시스템 프롬프트와 도구 정의는 요청마다 같으므로 프롬프트 캐시 지점을 둔다.
- 최종 응답 형식은 structured outputs(`output_config.format`)로 고정한다.
- 재시도는 SDK에 맡기지 않고 직접 한다(max_retries=0). 몇 번째 시도에 성공했는지 기록하고,
  지연 시간은 성공한 마지막 시도만 재기 위해서다.
- 응답의 model 이 요청한 모델과 다르면 실패로 처리한다. 다른 모델이 답한 결과를 같은 이름으로
  집계하지 않기 위해서다.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import anthropic

from batondesk.llm.base import LLMError, LLMRequest, LLMResponse, ToolCall, Usage


class AnthropicLLM:
    engine = "anthropic"

    def __init__(self, *, timeout_s: float = 60.0, max_attempts: int = 3, client: Any = None) -> None:
        self._client = client or anthropic.AsyncAnthropic(timeout=timeout_s, max_retries=0)
        self.max_attempts = max_attempts

    async def complete(self, request: LLMRequest) -> LLMResponse:
        kwargs = self._build(request)
        last_error: LLMError | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            try:
                message = await self._client.messages.create(**kwargs)
            except anthropic.RateLimitError as exc:
                last_error = LLMError(str(exc), kind="rate_limit", attempts=attempt)
            except anthropic.APITimeoutError as exc:
                last_error = LLMError(str(exc), kind="timeout", attempts=attempt)
            except anthropic.APIConnectionError as exc:
                last_error = LLMError(str(exc), kind="connection", attempts=attempt)
            except anthropic.AuthenticationError as exc:
                raise LLMError(str(exc), kind="auth", attempts=attempt) from exc
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:
                    last_error = LLMError(str(exc), kind="server", attempts=attempt)
                else:
                    raise LLMError(str(exc), kind="bad_request", attempts=attempt) from exc
            else:
                latency_ms = round((time.perf_counter() - started) * 1000, 1)
                return self._parse(message, request, latency_ms, attempt)
            if attempt < self.max_attempts:
                await asyncio.sleep(min(8.0, 0.6 * 2 ** (attempt - 1)) + random.random() * 0.4)
        assert last_error is not None
        raise last_error

    @staticmethod
    def _build(request: LLMRequest) -> dict[str, Any]:
        output_config: dict[str, Any] = {"effort": request.effort}
        if request.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.output_schema}
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}],
            "messages": request.messages,
            "thinking": {"type": "adaptive"},
            "output_config": output_config,
        }
        if request.tools:
            kwargs["tools"] = request.tools
        return kwargs

    @staticmethod
    def _parse(message: Any, request: LLMRequest, latency_ms: float, attempts: int) -> LLMResponse:
        served = str(message.model)
        if not served.startswith(request.model):
            raise LLMError(f"요청한 모델({request.model})과 응답 모델({served})이 다릅니다", kind="server", attempts=attempts)
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in message.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
        usage = message.usage
        stop_details = getattr(message, "stop_details", None)
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=str(message.stop_reason),
            model=served,
            usage=Usage(
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=int(usage.output_tokens or 0),
                cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            ),
            latency_ms=latency_ms,
            attempts=attempts,
            request_id=getattr(message, "_request_id", None),
            refusal_category=getattr(stop_details, "category", None) if stop_details else None,
            raw_content=message.content,
            engine="anthropic",
        )
