"""실제 모델 호출 점검(평가를 돌리기 전에 한 번).

평가 수십 건을 보낸 뒤에야 요청 형식이 틀렸다는 걸 알게 되는 일을 막는다. 세 가지를 본다:
1. 판정용 호출: structured outputs 만 쓰는 요청이 스키마대로 돌아오는가.
2. 에이전트용 호출: 실제 도구 정의를 보낸 요청에서 주문 조회 도구 호출이 나오는가.
3. 도구 결과를 돌려준 뒤 respond 도구로 최종 응답이 스키마대로 제출되는가.

    ANTHROPIC_API_KEY=... python scripts/llm_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from batondesk.agent.protocol import RESPOND_TOOL, AgentOutput, customer_block, respond_tool_definition
from batondesk.checks.resolution import RESOLUTION_SCHEMA
from batondesk.llm.anthropic_llm import AnthropicLLM
from batondesk.llm.base import LLMError, LLMRequest, LLMResponse
from batondesk.prompts.registry import PromptRegistry
from batondesk.settings import Settings
from batondesk.tools.specs import tool_definitions


def show(title: str, response: LLMResponse) -> None:
    usage = response.usage
    print(
        f"[{title}] model={response.model} stop={response.stop_reason} {response.latency_ms}ms attempts={response.attempts} "
        f"in={usage.input_tokens} out={usage.output_tokens} cache_read={usage.cache_read_input_tokens} "
        f"cache_write={usage.cache_creation_input_tokens} cost≈${response.cost_usd}"
    )


async def main() -> int:
    settings = Settings()
    prompts = PromptRegistry(settings.prompts_dir)
    llm = AnthropicLLM(timeout_s=settings.llm_timeout_s, max_attempts=2)
    try:
        judge = await llm.complete(
            LLMRequest(
                purpose="resolution",
                model=settings.judge_model,
                system=prompts.get("resolution").system,
                messages=[
                    {
                        "role": "user",
                        "content": "<대화>\n고객: 반품 기한이 며칠이에요?\nAI: 받은 날부터 7일 이내입니다.\n고객: 네 감사합니다\n</대화>",
                    }
                ],
                output_schema=RESOLUTION_SCHEMA,
                max_tokens=settings.judge_max_tokens,
                effort=settings.judge_effort,
            )
        )
        show("판정 호출", judge)
        print("   ", json.loads(judge.text))

        agent_prompt = prompts.get("agent", settings.agent_prompt)
        tools = [*tool_definitions(agent_prompt.tool_descriptions), respond_tool_definition()]
        messages: list[dict] = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": customer_block("BM-24011 주문 상태 알려 주세요.")},
                    {
                        "type": "text",
                        "text": "<상담_맥락>\n오늘: 2026-11-20 (금) 10:00 KST\n고객: 김하늘 (로그인 상태, 고객번호 C-1001)\n\n이번 메시지로 미리 검색한 지식 문서: 일치하는 문서 없음\n</상담_맥락>",
                    },
                ],
            }
        ]

        def request() -> LLMRequest:
            return LLMRequest(
                purpose="agent", model=settings.agent_model, system=agent_prompt.system, messages=messages, tools=tools,
                max_tokens=settings.agent_max_tokens, effort=settings.agent_effort,
            )  # fmt: skip

        first = await llm.complete(request())
        show("에이전트 호출 1", first)
        lookups = [c for c in first.tool_calls if c.name != RESPOND_TOOL]
        if first.stop_reason != "tool_use" or not lookups:
            print("   주문 조회 도구를 부르지 않았습니다:", first.text[:200], [(c.name, c.input) for c in first.tool_calls])
            return 1
        print("    도구 호출:", [(c.name, c.input) for c in first.tool_calls])
        messages.append({"role": "assistant", "content": first.raw_content})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(
                            {"evidence_id": "T1", "result": {"order_id": "BM-24011", "status_label": "배송완료", "paid_total": 42000}},
                            ensure_ascii=False,
                        ),
                    }
                    for call in lookups
                ],
            }
        )
        second = await llm.complete(request())
        show("에이전트 호출 2", second)
        final = next((c for c in second.tool_calls if c.name == RESPOND_TOOL), None)
        if final is None:
            print("   respond 도구로 응답을 제출하지 않았습니다:", second.text[:200], [(c.name, c.input) for c in second.tool_calls])
            return 1
        output = AgentOutput.model_validate(final.input)
        print("   ", output.model_dump())
        if not output.message.strip():
            print("   message 가 비어 있습니다")
            return 1
        if second.usage.cache_read_input_tokens == 0:
            print("    참고: 두 번째 호출에서 캐시 읽기가 0입니다(시스템 프롬프트가 캐시 최소 길이보다 짧을 수 있음).")
    except LLMError as exc:
        print(f"모델 호출 실패: kind={exc.kind} attempts={exc.attempts}\n{exc}", file=sys.stderr)
        return 1
    print("\n모델 호출 점검 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
