"""대화 해결 여부 판정.

정의(업계 제품들이 쓰는 구분을 따랐다 — docs/research.md):
- 상담원에게 넘어간 대화는 AI가 해결한 것으로 세지 않는다(handed_off).
- 고객이 해결됐다고 표현한 경우(resolved_confirmed)와, 답을 받고 말없이 끝난 경우
  (resolved_assumed)를 따로 센다. 뒤쪽은 "해결됐다고 가정"일 뿐이라는 점을 지표에서 드러낸다.
- 인사뿐이거나 상담과 무관한 대화는 분모에서 뺀다(not_applicable).

LLM 판정은 "해결됐다"고 너무 쉽게 말하는 경향이 알려져 있어, 평가에서는 정확도뿐 아니라
미해결을 미해결로 맞히는 비율(특이도)을 따로 본다.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from batondesk.llm.base import LLMClient, LLMError, LLMRequest
from batondesk.prompts.registry import PromptVersion

ResolutionStatus = Literal[
    "resolved_confirmed", "resolved_assumed", "unresolved", "pending_customer", "not_applicable", "handed_off", "unknown"
]
RESOLVED = ("resolved_confirmed", "resolved_assumed")

RESOLUTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["resolved_confirmed", "resolved_assumed", "unresolved", "pending_customer", "not_applicable"],
        },
        "customer_signal": {
            "type": "string",
            "enum": ["positive", "negative", "none"],
            "description": "고객의 마지막 반응: 만족·감사(positive), 불만·반박(negative), 표현 없음(none)",
        },
        "reason": {"type": "string", "description": "판정 이유 한두 문장"},
    },
    "required": ["status", "customer_signal", "reason"],
    "additionalProperties": False,
}


class ResolutionResult(BaseModel):
    status: ResolutionStatus
    customer_signal: Literal["positive", "negative", "none"] = "none"
    reason: str = ""
    checker: dict[str, Any] = Field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED

    @property
    def counted(self) -> bool:
        """해결률의 분모에 들어가는가."""
        return self.status in (*RESOLVED, "unresolved", "handed_off")


def render_transcript(messages: list[dict[str, str]]) -> str:
    return "\n".join(f"{m['role']}: {m['text']}" for m in messages)


class ResolutionChecker:
    def __init__(self, llm: LLMClient | None, prompt: PromptVersion, model: str, *, effort: str = "low", max_tokens: int = 2000) -> None:
        self.llm = llm
        self.prompt = prompt
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    async def check(
        self, messages: list[dict[str, str]], *, handed_off: bool = False
    ) -> tuple[ResolutionResult, list[dict[str, Any]]]:
        if handed_off:
            return ResolutionResult(status="handed_off", reason="상담원에게 인계된 대화", checker={"engine": "rules"}), []
        if not any(m["role"] == "고객" for m in messages):
            return ResolutionResult(status="not_applicable", reason="고객 메시지 없음", checker={"engine": "rules"}), []
        if self.llm is None:
            return ResolutionResult(status="unknown", reason="판정 모델을 쓸 수 없음", checker={"engine": "none"}), []
        request = LLMRequest(
            purpose="resolution",
            model=self.model,
            system=self.prompt.system,
            messages=[{"role": "user", "content": f"<대화>\n{render_transcript(messages)}\n</대화>"}],
            output_schema=RESOLUTION_SCHEMA,
            max_tokens=self.max_tokens,
            effort=self.effort,
        )
        steps: list[dict[str, Any]] = []
        try:
            response = await self.llm.complete(request)
            steps.append(response.trace("resolution"))
            if response.stop_reason in ("refusal", "max_tokens"):
                raise LLMError(f"판정 응답이 완결되지 않았습니다: {response.stop_reason}", kind="server")
            data = json.loads(response.text)
            result = ResolutionResult(
                status=data["status"],
                customer_signal=data["customer_signal"],
                reason=data["reason"],
                checker={
                    "engine": response.engine,
                    "model": response.model,
                    "prompt": self.prompt.id,
                    "prompt_sha256": self.prompt.sha256[:12],
                    "latency_ms": response.latency_ms,
                    "cost_usd": response.cost_usd,
                },
            )
        except (LLMError, ValidationError, ValueError, KeyError) as exc:
            steps.append({"type": "check_error", "check": "resolution", "error": f"{type(exc).__name__}: {exc}"[:300]})
            result = ResolutionResult(status="unknown", reason="판정 실패", checker={"engine": "error"})
        return result, steps
