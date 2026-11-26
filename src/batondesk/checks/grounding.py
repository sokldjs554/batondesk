"""근거 일치 판정(환각 탐지).

두 겹으로 본다.
1. 규칙: 답변의 금액·기간·날짜가 근거에 있는지 글자 단위로 대조(facts.py).
2. LLM: 답변을 주장으로 나누고 주장마다 supported / contradicted / unsupported / not_factual 로 판정.

최종 판정(verdict)은 모델이 내지 않고 주장별 라벨을 코드가 합친다. "전체적으로 괜찮아 보인다"는
식의 뭉뚱그린 판단을 막고, 어느 문장 때문에 걸렸는지 남기기 위해서다.

판정기가 틀릴 수 있다는 전제로 쓴다: 판정 결과에는 판정기의 모델·프롬프트 버전을 함께 남기고,
판정기 자체의 정답률은 evals/ 의 라벨된 자료로 따로 잰다(docs/evaluation.md).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from batondesk.agent.evidence import Evidence
from batondesk.checks.facts import FactFlag, check_facts
from batondesk.llm.base import LLMClient, LLMError, LLMRequest
from batondesk.prompts.registry import PromptVersion

ClaimLabel = Literal["supported", "contradicted", "unsupported", "not_factual"]
Verdict = Literal["grounded", "ungrounded"]

GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string", "description": "답변에서 뽑은 주장 한 개(원문 표현에 가깝게)"},
                    "label": {"type": "string", "enum": ["supported", "contradicted", "unsupported", "not_factual"]},
                    "evidence_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string", "description": "판정 이유 한 문장"},
                },
                "required": ["claim", "label", "evidence_ids", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


class ClaimVerdict(BaseModel):
    claim: str
    label: ClaimLabel
    evidence_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class GroundingResult(BaseModel):
    verdict: Verdict
    mode: Literal["llm+rules", "rules_only"]
    claims: list[ClaimVerdict] = Field(default_factory=list)
    contradicted: int = 0
    unsupported: int = 0
    fact_flags: list[dict[str, Any]] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)  # 사람이 읽을 요약(재작성 지시와 상담원 화면에 쓴다)
    checker: dict[str, Any] = Field(default_factory=dict)

    @property
    def grounded(self) -> bool:
        return self.verdict == "grounded"


WEEKDAYS = "월화수목금토일"


def _today_text(today: datetime | None) -> str:
    """판정기가 날짜 계산("기한까지 4일 남았습니다")을 확인하려면 오늘이 며칠인지 알아야 한다."""
    if today is None:
        return ""
    return f"{today.strftime('%Y-%m-%d')} ({WEEKDAYS[today.weekday()]}) {today.month}월 {today.day}일"


def _render_input(
    answer: str, evidence: list[Evidence], conversation: list[dict[str, str]], flags: list[FactFlag], today: datetime | None
) -> str:
    parts = []
    if today is not None:
        parts.append(f"<오늘>{_today_text(today)}</오늘>")
    parts.append("<근거>")
    parts.extend(e.for_model() for e in evidence)
    if not evidence:
        parts.append("(근거 없음)")
    parts.append("</근거>")
    parts.append("<대화>")
    parts.extend(f"{m['role']}: {m['text']}" for m in conversation)
    parts.append("</대화>")
    parts.append("<답변>")
    parts.append(answer)
    parts.append("</답변>")
    if flags:
        surfaces = ", ".join(sorted({f.surface for f in flags}))
        parts.append(
            f"<참고>규칙 검사에서 다음 표현을 근거에서 글자 그대로 찾지 못했습니다: {surfaces}. "
            "근거의 값으로 계산해 나오는 값인지, 근거에 없는 값인지 직접 확인하세요.</참고>"
        )
    return "\n".join(parts)


class GroundingChecker:
    def __init__(self, llm: LLMClient | None, prompt: PromptVersion, model: str, *, effort: str = "low", max_tokens: int = 3000) -> None:
        self.llm = llm
        self.prompt = prompt
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    async def check(
        self,
        answer: str,
        evidence: list[Evidence],
        conversation: list[dict[str, str]] | None = None,
        *,
        use_llm: bool | Literal["auto"] = True,
        today: datetime | None = None,
    ) -> tuple[GroundingResult, list[dict[str, Any]]]:
        """(판정, 실행 기록 단계들)을 돌려준다.

        use_llm="auto" 는 규칙 검사에서 근거에 없는 값이 하나라도 나왔을 때만 LLM 판정을 부른다.
        되묻기·인계 안내처럼 사실 주장이 적은 메시지에 매번 모델을 쓰지 않기 위한 선별이다.
        """
        conversation = conversation or []
        customer_said = "\n".join(m["text"] for m in conversation if m["role"] == "고객")
        flags = check_facts(
            answer,
            [(f"{e.title}\n{e.text}", e.kind == "tool") for e in evidence],
            extra_context=f"{customer_said}\n{_today_text(today)}",
        )
        flag_dicts = [f.as_dict() for f in flags]
        hard = [f for f in flags if f.hard]
        steps: list[dict[str, Any]] = []
        if use_llm == "auto":
            use_llm = bool(flags)

        if use_llm and self.llm is not None:
            request = LLMRequest(
                purpose="grounding",
                model=self.model,
                system=self.prompt.system,
                messages=[{"role": "user", "content": _render_input(answer, evidence, conversation, flags, today)}],
                output_schema=GROUNDING_SCHEMA,
                max_tokens=self.max_tokens,
                effort=self.effort,
            )
            try:
                response = await self.llm.complete(request)
                steps.append(response.trace("grounding"))
                if response.stop_reason in ("refusal", "max_tokens"):
                    raise LLMError(f"판정 응답이 완결되지 않았습니다: {response.stop_reason}", kind="server")
                claims = [ClaimVerdict.model_validate(c) for c in json.loads(response.text)["claims"]]
            except (LLMError, ValidationError, ValueError, KeyError) as exc:
                steps.append({"type": "check_error", "check": "grounding", "error": f"{type(exc).__name__}: {exc}"[:300]})
            else:
                contradicted = [c for c in claims if c.label == "contradicted"]
                unsupported = [c for c in claims if c.label == "unsupported"]
                problems = [f"근거와 어긋남: {c.claim} — {c.reason}" for c in contradicted]
                problems += [f"근거에 없음: {c.claim} — {c.reason}" for c in unsupported]
                result = GroundingResult(
                    verdict="ungrounded" if (contradicted or unsupported) else "grounded",
                    mode="llm+rules",
                    claims=claims,
                    contradicted=len(contradicted),
                    unsupported=len(unsupported),
                    fact_flags=flag_dicts,
                    problems=problems,
                    checker={
                        "engine": response.engine,
                        "model": response.model,
                        "prompt": self.prompt.id,
                        "prompt_sha256": self.prompt.sha256[:12],
                        "latency_ms": response.latency_ms,
                        "cost_usd": response.cost_usd,
                    },
                )
                return result, steps

        # LLM 판정을 쓰지 않았거나 실패했다: 규칙만으로, 굳은 사실이 어긋나면 막는다(닫힌 쪽으로 실패).
        result = GroundingResult(
            verdict="ungrounded" if hard else "grounded",
            mode="rules_only",
            fact_flags=flag_dicts,
            problems=[f"근거에서 찾지 못한 값: {f.surface}" for f in hard],
            checker={"engine": "rules", "model": None, "prompt": None},
        )
        return result, steps
