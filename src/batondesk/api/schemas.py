"""API 요청 본문 모델."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    customer_id: str = Field(description="예시 고객 ID. GET /v1/demo/customers 로 목록을 본다.", examples=["C-1001"])
    channel: str = Field(default="web", description="문의가 들어온 채널(web, widget, api, webhook)")


class MessageCreate(BaseModel):
    text: str = Field(description="고객 메시지", examples=["BM-24011 반품하고 싶어요"])


class FeedbackCreate(BaseModel):
    resolved: bool = Field(description="고객이 누른 버튼: 해결됐어요(true) / 아직이에요(false)")


class AgentAction(BaseModel):
    agent_name: str = Field(default="데모 상담원", max_length=40)


class AgentReply(AgentAction):
    text: str


class HandoffResolve(AgentAction):
    note: str = Field(default="", description="처리 메모")
    return_to_ai: bool = Field(default=False, description="true 면 대화를 닫지 않고 AI에게 돌려준다")


class KnowledgeSearch(BaseModel):
    query: str
    k: int = Field(default=5, ge=1, le=10)
    mode: Literal["hybrid", "bm25", "ngram"] = "hybrid"


class EvidenceIn(BaseModel):
    id: str = Field(examples=["K1"])
    title: str = ""
    text: str
    kind: Literal["kb", "tool"] = "kb"


class TranscriptMessage(BaseModel):
    role: Literal["고객", "AI", "상담원"]
    text: str


class GroundingCheckRequest(BaseModel):
    answer: str = Field(description="검사할 답변")
    evidence: list[EvidenceIn] = Field(description="답변이 쓸 수 있었던 근거")
    conversation: list[TranscriptMessage] = Field(default_factory=list)
    use_llm: bool = Field(default=True, description="false 면 규칙 검사만 한다(모델 호출 없음)")
    today: date | None = Field(default=None, description="답변이 날짜를 계산해 말할 때 기준이 되는 날. 예: 2026-11-20")
    customer_name: str | None = Field(
        default=None, description="답변이 부르는 고객 이름(알려 주지 않으면 이름을 부른 것이 근거 없음으로 잡힐 수 있다)"
    )


class ResolutionCheckRequest(BaseModel):
    messages: list[TranscriptMessage]
    handed_off: bool = False


class WebhookMessage(BaseModel):
    channel: str = Field(default="webhook", examples=["kakao", "sendbird", "slack"])
    external_user_key: str = Field(description="채널 쪽 사용자 식별자")
    customer_id: str = Field(description="채널이 인증한 고객 ID(예시 고객)")
    text: str


class FaultSet(BaseModel):
    mode: Literal["none", "timeout", "error", "flaky", "slow"]
