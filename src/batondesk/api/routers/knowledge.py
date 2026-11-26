"""지식 검색 API와 판정 마이크로 API(근거 일치, 해결 여부)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request

from batondesk.agent.evidence import Evidence
from batondesk.api.deps import client_key, get_service, guard_llm
from batondesk.api.schemas import GroundingCheckRequest, KnowledgeSearch, ResolutionCheckRequest
from batondesk.clock import KST
from batondesk.service import ServiceError, SupportService

router = APIRouter(prefix="/v1", tags=["지식 검색·판정"])


@router.post("/kb/search", summary="지식 문서 검색")
async def kb_search(body: KnowledgeSearch, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    today = service.commerce_store.sandbox("demo").now().date()
    hits = service.kb.search(body.query, k=body.k, mode=body.mode)
    return {
        "query": body.query,
        "mode": body.mode,
        "hits": [
            {
                "chunk_id": h.chunk.chunk_id,
                "doc_id": h.chunk.doc_id,
                "title": h.chunk.title,
                "heading": h.chunk.heading,
                "doc_type": h.chunk.doc_type,
                "validity": h.chunk.validity_label(today),
                "text": h.chunk.text,
                "score": round(h.score, 5),
                "bm25_rank": h.bm25_rank,
                "ngram_rank": h.ngram_rank,
            }
            for h in hits
        ],
    }


@router.get("/kb/documents", summary="지식 문서 목록")
async def kb_documents(service: SupportService = Depends(get_service)) -> dict[str, Any]:
    today = service.commerce_store.sandbox("demo").now().date()
    return {
        "documents": [
            {
                "doc_id": d.doc_id,
                "title": d.title,
                "category": d.category,
                "doc_type": d.doc_type,
                "effective_from": d.effective_from.isoformat(),
                "effective_to": d.effective_to.isoformat() if d.effective_to else None,
                "in_effect": d.is_effective(today),
                "chunks": len(service.kb.chunks_of(d.doc_id)),
            }
            for d in service.kb.docs.values()
        ]
    }


@router.get("/kb/documents/{doc_id}", summary="지식 문서 한 건")
async def kb_document(doc_id: str, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    doc = service.kb.docs.get(doc_id)
    if doc is None:
        raise ServiceError(404, "document_not_found", "문서를 찾을 수 없습니다.")
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "category": doc.category,
        "doc_type": doc.doc_type,
        "effective_from": doc.effective_from.isoformat(),
        "effective_to": doc.effective_to.isoformat() if doc.effective_to else None,
        "sections": [{"chunk_id": c.chunk_id, "heading": c.heading, "text": c.text} for c in service.kb.chunks_of(doc_id)],
    }


@router.post(
    "/checks/grounding",
    summary="근거 일치 판정(환각 탐지)",
    description="답변을 주장 단위로 나눠 근거가 뒷받침하는지 판정한다. 에이전트와 무관하게 단독으로 부를 수 있다. "
    "모델을 쓸 수 없거나 `use_llm=false` 면 규칙 검사(금액·기간·날짜 대조)만 하고 `mode` 가 `rules_only` 로 온다.",
)
async def check_grounding(body: GroundingCheckRequest, request: Request, service: SupportService = Depends(get_service)) -> dict[str, Any]:
    if body.use_llm:
        guard_llm(service, client_key(request))
    evidence = [Evidence(id=e.id, kind=e.kind, title=e.title, text=e.text, turn_seq=0) for e in body.evidence]
    conversation = [m.model_dump() for m in body.conversation]
    today = datetime.combine(body.today, datetime.min.time(), tzinfo=KST) if body.today else None
    result, _ = await service.grounding_checker().check(
        body.answer, evidence, conversation, use_llm=body.use_llm, today=today, customer_name=body.customer_name
    )
    return result.model_dump()


@router.post(
    "/checks/resolution",
    summary="대화 해결 여부 판정",
    description="대화 기록을 읽고 resolved_confirmed / resolved_assumed / unresolved / pending_customer / "
    "not_applicable / handed_off 중 하나로 판정한다.",
)
async def check_resolution(
    body: ResolutionCheckRequest, request: Request, service: SupportService = Depends(get_service)
) -> dict[str, Any]:
    guard_llm(service, client_key(request))
    result, _ = await service.resolution_checker().check([m.model_dump() for m in body.messages], handed_off=body.handed_off)
    return {**result.model_dump(), "resolved": result.resolved, "counted_in_rate": result.counted}
