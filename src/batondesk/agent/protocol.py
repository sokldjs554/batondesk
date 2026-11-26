"""에이전트가 내놓는 최종 출력의 형식과, 모델에 보내는 메시지 조립.

최종 출력은 `respond` 도구 호출로 받는다(strict 스키마). 고객에게 보이는 글(message)과 시스템이 읽는
판단(action, 인계 사유, 인용한 근거)을 분리해 두어야 인계·근거 검사·집계를 코드로 처리할 수 있다.

처음에는 structured outputs(JSON 출력 형식)로 받았다. 실제 API로 돌려 보니 JSON 출력 형식과 도구를
함께 준 요청에서 모델이 필요한 도구 호출을 건너뛰고 빈 답을 내는 경우가 있었다(공급자 문서에도 적힌
동작). 그래서 "답도 도구로 제출"하게 바꿨다 — 모델은 처음부터 끝까지 도구 호출 방식으로만 움직인다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from batondesk.agent.evidence import Evidence

Action = Literal["answer", "ask", "handoff"]

# 모델이 고를 수 있는 인계 사유
MODEL_HANDOFF_REASONS: tuple[str, ...] = (
    "none",
    "customer_request",  # 고객이 상담원을 직접 요청
    "policy_exception",  # 정책 밖의 예외·보상 요구라 사람의 판단이 필요
    "needs_human_review",  # 금액 확정·결제 내역·본인 확인처럼 사람이 직접 확인해야 하는 일
    "tool_failure",  # 주문 시스템 오류로 확인 불가
    "no_knowledge",  # 지식 문서에 답이 없음
    "conflicting_knowledge",  # 문서끼리 내용이 달라 어느 쪽인지 정할 수 없음
    "order_not_found",  # 주문을 끝내 확인하지 못함
    "safety_or_legal",  # 안전 사고·법적 대응
    "repeated_failure",  # 같은 문제로 반복해서 해결하지 못함
)
# 시스템이 붙이는 인계 사유(모델 출력이 아니라 파이프라인이 결정)
SYSTEM_HANDOFF_REASONS: tuple[str, ...] = ("ungrounded_answer", "agent_error")

HANDOFF_REASON_LABEL: dict[str, str] = {
    "customer_request": "고객이 상담원을 요청",
    "policy_exception": "정책 밖 예외·보상 요구",
    "needs_human_review": "사람의 확인이 필요한 건",
    "tool_failure": "주문 시스템 오류",
    "no_knowledge": "지식 문서에 답이 없음",
    "conflicting_knowledge": "문서 내용이 서로 다름",
    "order_not_found": "주문을 확인하지 못함",
    "safety_or_legal": "안전·법적 문제",
    "repeated_failure": "반복 미해결",
    "ungrounded_answer": "근거와 맞지 않는 답변이 걸러짐",
    "agent_error": "AI 처리 오류",
}

AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["answer", "ask", "handoff"],
            "description": "answer: 근거로 답함 / ask: 답하려면 고객에게 받아야 할 정보가 있음 / handoff: 상담원에게 넘김",
        },
        "message": {"type": "string", "description": "고객에게 그대로 보낼 한국어 메시지"},
        "citations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "message 의 내용을 뒷받침하는 근거 ID 목록(예: K1, T2). 근거 없이 쓴 문장이 없어야 한다.",
        },
        "handoff_reason": {"type": "string", "enum": list(MODEL_HANDOFF_REASONS)},
        "handoff_summary": {
            "type": "string",
            "description": "action 이 handoff 일 때 상담원이 읽을 요약: 고객 요청, 확인한 사실, 넘기는 이유, 권하는 다음 조치. 아니면 빈 문자열.",
        },
        "kb_issue": {
            "type": "string",
            "description": "지식 문서끼리 내용이 다르거나 낡은 문서를 발견했으면 운영자가 볼 메모. 없으면 빈 문자열.",
        },
    },
    "required": ["action", "message", "citations", "handoff_reason", "handoff_summary", "kb_issue"],
    "additionalProperties": False,
}


RESPOND_TOOL = "respond"
RESPOND_TOOL_DESCRIPTION = (
    "이번 턴의 최종 응답을 제출한다. 고객에게 보낼 글은 반드시 이 도구로만 내보낸다. "
    "필요한 근거(지식 문서, 주문 조회 결과)를 모두 확보한 뒤 마지막에 한 번 부른다. "
    "다른 도구와 같은 응답에서 함께 부르지 않는다."
)


def respond_tool_definition(description: str | None = None) -> dict[str, Any]:
    return {
        "name": RESPOND_TOOL,
        "description": description or RESPOND_TOOL_DESCRIPTION,
        "strict": True,
        "input_schema": AGENT_OUTPUT_SCHEMA,
    }


class AgentOutput(BaseModel):
    action: Action
    message: str
    citations: list[str] = Field(default_factory=list)
    handoff_reason: str = "none"
    handoff_summary: str = ""
    kb_issue: str = ""


WEEKDAYS = "월화수목금토일"


def customer_block(text: str) -> str:
    return f"<고객_메시지>\n{text}\n</고객_메시지>"


def context_block(
    *,
    now: datetime,
    customer_name: str,
    customer_id: str,
    query: str,
    fresh: list[Evidence],
    carried: list[Evidence],
) -> str:
    """이번 턴에 모델이 참고할 맥락. 고객 메시지와 같은 user 턴에 별도 블록으로 붙인다."""
    lines = [
        "<상담_맥락>",
        f"오늘: {now.strftime('%Y-%m-%d')} ({WEEKDAYS[now.weekday()]}) {now.strftime('%H:%M')} KST",
        f"고객: {customer_name} (로그인 상태, 고객번호 {customer_id})",
    ]
    if carried:
        lines.append("")
        lines.append("이전 턴까지 확보한 근거:")
        lines.extend(e.for_model() for e in carried)
    lines.append("")
    if fresh:
        lines.append(f'이번 메시지로 미리 검색한 지식 문서 (질의: "{query}"):')
        lines.extend(e.for_model() for e in fresh)
    else:
        lines.append("이번 메시지로 미리 검색한 지식 문서: 일치하는 문서 없음")
    lines.append("</상담_맥락>")
    return "\n".join(lines)
