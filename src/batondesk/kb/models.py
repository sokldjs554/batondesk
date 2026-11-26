"""지식 문서와 청크 모델."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

DocType = Literal["policy", "notice", "faq", "guide"]

# 문서끼리 내용이 다를 때 어느 쪽을 따를지의 기본 순서. 숫자가 작을수록 우선한다.
# 공지(notice)는 "정해진 기간·대상에 한해" 정책을 덮어쓰므로 정책보다 뒤에 두고,
# 적용 범위 판단은 에이전트 프롬프트와 문서 본문(적용 기간)에 맡긴다.
DOC_TYPE_RANK: dict[str, int] = {"policy": 0, "notice": 1, "guide": 2, "faq": 3}


@dataclass(frozen=True)
class KnowledgeDoc:
    doc_id: str
    title: str
    category: str
    doc_type: DocType
    effective_from: date
    effective_to: date | None
    body: str
    path: str

    def is_effective(self, today: date) -> bool:
        if today < self.effective_from:
            return False
        return self.effective_to is None or today <= self.effective_to


@dataclass(frozen=True)
class Chunk:
    chunk_id: str  # "{doc_id}#{n}"
    doc_id: str
    title: str
    heading: str
    text: str
    category: str
    doc_type: DocType
    effective_from: date
    effective_to: date | None

    def validity_label(self, today: date) -> str:
        if today < self.effective_from:
            return f"시행 전 ({self.effective_from.isoformat()}부터)"
        if self.effective_to is not None and today > self.effective_to:
            return f"적용 종료 ({self.effective_to.isoformat()}까지)"
        if self.effective_to is not None:
            return f"기간 한정 ({self.effective_from.isoformat()} ~ {self.effective_to.isoformat()})"
        return f"현행 ({self.effective_from.isoformat()}부터)"


@dataclass
class SearchHit:
    chunk: Chunk
    score: float
    bm25_rank: int | None = None
    ngram_rank: int | None = None
    bm25_score: float | None = None
    extra: dict[str, object] = field(default_factory=dict)
