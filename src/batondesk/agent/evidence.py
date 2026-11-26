"""근거(evidence) 등록부.

에이전트가 답변에 쓸 수 있는 근거는 두 종류뿐이다: 검색된 지식 문서 청크(K*)와
도구 호출 결과(T*). 각 근거에 짧은 ID를 붙여 모델이 인용하게 하고, 같은 ID로
근거 일치 검사·화면의 출처 표시·상담원 화면의 근거 목록을 모두 만든다.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from batondesk.kb.models import Chunk

DOC_TYPE_LABEL = {"policy": "정책", "notice": "공지", "faq": "자주 묻는 질문", "guide": "안내"}


class Evidence(BaseModel):
    id: str
    kind: Literal["kb", "tool"]
    title: str
    text: str
    turn_seq: int
    meta: dict[str, Any] = Field(default_factory=dict)

    def for_model(self) -> str:
        return f"[{self.id}] {self.title}\n{self.text}"


class EvidencePool:
    def __init__(self, items: list[Evidence] | None = None) -> None:
        self._items: dict[str, Evidence] = {e.id: e for e in (items or [])}
        self._by_chunk: dict[str, str] = {e.meta["chunk_id"]: e.id for e in self._items.values() if e.kind == "kb"}

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, evidence_id: str) -> bool:
        return evidence_id in self._items

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def _next_id(self, prefix: str) -> str:
        n = sum(1 for e in self._items.values() if e.id.startswith(prefix)) + 1
        return f"{prefix}{n}"

    def add_chunk(
        self, chunk: Chunk, today: date, turn_seq: int, query: str, score: float, *, expanded_from: str | None = None
    ) -> Evidence:
        existing = self._by_chunk.get(chunk.chunk_id)
        if existing:
            return self._items[existing]
        ev = Evidence(
            id=self._next_id("K"),
            kind="kb",
            title=f"{chunk.title} › {chunk.heading} ({DOC_TYPE_LABEL[chunk.doc_type]} · {chunk.validity_label(today)})",
            text=chunk.text,
            turn_seq=turn_seq,
            meta={
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "doc_title": chunk.title,
                "heading": chunk.heading,
                "doc_type": chunk.doc_type,
                "effective_from": chunk.effective_from.isoformat(),
                "effective_to": chunk.effective_to.isoformat() if chunk.effective_to else None,
                "in_effect": chunk.effective_from <= today and (chunk.effective_to is None or today <= chunk.effective_to),
                "query": query,
                "score": round(score, 5),
                # 검색 순위로 들어온 것이 아니라, 같은 공지의 다른 절이 걸려서 함께 들어온 경우 그 절의 ID
                "expanded_from": expanded_from if expanded_from != chunk.chunk_id else None,
            },
        )
        self._items[ev.id] = ev
        self._by_chunk[chunk.chunk_id] = ev.id
        return ev

    def add_tool_result(self, tool: str, args: dict[str, Any], result: dict[str, Any], turn_seq: int, status: str) -> Evidence:
        arg_text = ", ".join(f"{k}={v}" for k, v in args.items()) or "인자 없음"
        ev = Evidence(
            id=self._next_id("T"),
            kind="tool",
            title=f"예시 주문 시스템 · {tool}({arg_text})",
            text=json.dumps(result, ensure_ascii=False, sort_keys=True),
            turn_seq=turn_seq,
            meta={"tool": tool, "args": args, "status": status},
        )
        self._items[ev.id] = ev
        return ev

    def dump(self) -> list[dict[str, Any]]:
        return [e.model_dump() for e in self._items.values()]

    @classmethod
    def load(cls, raw: list[dict[str, Any]] | None) -> EvidencePool:
        return cls([Evidence.model_validate(r) for r in (raw or [])])
