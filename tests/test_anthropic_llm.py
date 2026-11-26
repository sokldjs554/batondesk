"""Claude 호출부를 실제 SDK에 물려 확인한다(네트워크만 가짜).

실제 API에는 닿지 않는다. HTTP 전송 계층을 가짜로 바꿔, SDK가 우리 인자를 받아 어떤 요청 본문을
만드는지와 응답을 어떻게 풀어 주는지를 본다. 인자 이름이 틀리면 여기서 걸린다.
실제 서버가 이 요청을 받아들이는지는 scripts/llm_smoke.py 가 본다.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
import httpx2
import pytest

from batondesk.agent.protocol import AGENT_OUTPUT_SCHEMA
from batondesk.llm.anthropic_llm import AnthropicLLM
from batondesk.llm.base import LLMError, LLMRequest
from batondesk.tools.specs import tool_definitions


def _message(content: list[dict[str, Any]], *, stop_reason: str = "end_turn", model: str = "claude-haiku-5-5") -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1200, "output_tokens": 80, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0},
    }


def _llm(handler: Any, *, max_attempts: int = 3) -> AnthropicLLM:
    client = anthropic.AsyncAnthropic(
        api_key="test-key", max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    )
    return AnthropicLLM(client=client, max_attempts=max_attempts)


def _request(**overrides: Any) -> LLMRequest:
    base: dict[str, Any] = {
        "purpose": "agent",
        "model": "claude-haiku-5-5",
        "system": "시스템 프롬프트",
        "messages": [{"role": "user", "content": "BM-24011 상태 알려 주세요"}],
        "tools": tool_definitions(),
        "output_schema": AGENT_OUTPUT_SCHEMA,
        "max_tokens": 4000,
        "effort": "low",
    }
    return LLMRequest(**{**base, **overrides})


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr("batondesk.llm.anthropic_llm.asyncio.sleep", instant)


async def test_request_body_and_tool_use_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        seen["version"] = request.headers.get("anthropic-version")
        content = [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_order", "input": {"order_id": "BM-24011"}},
        ]
        return httpx2.Response(200, json=_message(content, stop_reason="tool_use"), headers={"request-id": "req_abc"})

    response = await _llm(handler).complete(_request())

    body = seen["body"]
    assert seen["url"].endswith("/v1/messages") and seen["auth"] == "Bearer test-key"
    assert seen["version"] == "2026-10-21", "SDK 2.x 가 보내는 API 버전. 이 버전은 온도 등 표본 추출 인자를 받지 않는다"
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 4000
    assert body["system"] == [{"type": "text", "text": "시스템 프롬프트", "cache_control": {"type": "ephemeral"}}]
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": AGENT_OUTPUT_SCHEMA}
    assert [t["name"] for t in body["tools"]][:2] == ["search_knowledge", "list_my_orders"]
    assert all(t["strict"] is True for t in body["tools"])
    assert "temperature" not in body, "이 세대의 모델은 온도 값을 받지 않는다"

    assert response.stop_reason == "tool_use" and response.model == "claude-haiku-5-5"
    assert [(c.id, c.name, c.input) for c in response.tool_calls] == [("toolu_1", "get_order", {"order_id": "BM-24011"})]
    assert response.usage.as_dict() == {
        "input_tokens": 1200,
        "output_tokens": 80,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 0,
    }
    assert response.request_id == "req_abc" and response.attempts == 1 and response.engine == "anthropic"
    assert response.cost_usd == pytest.approx((1200 * 0.10 + 80 * 0.50 + 900 * 0.01) / 1_000_000)
    assert [block.type for block in response.raw_content] == ["thinking", "tool_use"], "같은 턴의 다음 요청에 그대로 되돌려 보낼 원본"


async def test_raw_content_round_trips_into_the_next_request() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            content = [
                {"type": "thinking", "thinking": "", "signature": "sig-1"},
                {"type": "tool_use", "id": "toolu_1", "name": "get_order", "input": {"order_id": "BM-24011"}},
            ]
            return httpx2.Response(200, json=_message(content, stop_reason="tool_use"))
        final = {
            "action": "answer",
            "message": "배송완료입니다.",
            "citations": ["T1"],
            "handoff_reason": "none",
            "handoff_summary": "",
            "kb_issue": "",
        }
        return httpx2.Response(200, json=_message([{"type": "text", "text": json.dumps(final, ensure_ascii=False)}]))

    llm = _llm(handler)
    request = _request()
    first = await llm.complete(request)
    request.messages = [
        *request.messages,
        {"role": "assistant", "content": first.raw_content},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"evidence_id": "T1"}', "is_error": False}],
        },
    ]
    second = await llm.complete(request)
    replayed = bodies[1]["messages"][1]["content"]
    assert [b["type"] for b in replayed] == ["thinking", "tool_use"]
    assert replayed[0]["signature"] == "sig-1", "생각 블록은 고치지 않고 그대로 돌려보낸다"
    assert bodies[1]["messages"][2]["content"][0]["type"] == "tool_result"
    assert json.loads(second.text)["action"] == "answer" and not second.tool_calls


async def test_no_tools_and_no_schema_are_omitted() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=_message([{"type": "text", "text": "ok"}]))

    await _llm(handler).complete(_request(tools=[], output_schema=None))
    assert "tools" not in seen["body"] and seen["body"]["output_config"] == {"effort": "low"}


async def test_transient_errors_are_retried_and_counted() -> None:
    calls = {"n": 0}

    def handler(_: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
        if calls["n"] == 2:
            return httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}})
        return httpx2.Response(200, json=_message([{"type": "text", "text": "ok"}]))

    response = await _llm(handler).complete(_request(tools=[], output_schema=None))
    assert response.attempts == 3 and calls["n"] == 3


@pytest.mark.parametrize(
    ("status", "error_type", "kind", "attempts"),
    [
        (401, "authentication_error", "auth", 1),
        (400, "invalid_request_error", "bad_request", 1),
        (500, "api_error", "server", 2),
        (429, "rate_limit_error", "rate_limit", 2),
    ],
)
async def test_errors_map_to_kinds(status: int, error_type: str, kind: str, attempts: int) -> None:
    calls = {"n": 0}

    def handler(_: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return httpx2.Response(status, json={"type": "error", "error": {"type": error_type, "message": "x"}})

    with pytest.raises(LLMError) as excinfo:
        await _llm(handler, max_attempts=2).complete(_request(tools=[], output_schema=None))
    assert excinfo.value.kind == kind and calls["n"] == attempts, "인증·요청 오류는 다시 시도하지 않는다"


async def test_a_different_served_model_is_rejected() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_message([{"type": "text", "text": "ok"}], model="claude-sonnet-5-5"))

    with pytest.raises(LLMError, match="다릅니다"):
        await _llm(handler).complete(_request(tools=[], output_schema=None))


async def test_refusal_is_returned_as_a_model_outcome() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        payload = _message([], stop_reason="refusal")
        payload["stop_details"] = {"type": "refusal", "category": "general_harms", "explanation": None}
        return httpx2.Response(200, json=payload)

    response = await _llm(handler).complete(_request(tools=[], output_schema=None))
    assert response.stop_reason == "refusal" and response.refusal_category == "general_harms" and response.text == ""
