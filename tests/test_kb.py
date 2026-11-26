from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from batondesk.kb.loader import KnowledgeFormatError, parse_doc, split_chunks
from batondesk.kb.retriever import KnowledgeBase, rrf_fuse
from batondesk.kb.tokenizer import KiwiTokenizer


def test_all_documents_load_with_unique_ids(kb: KnowledgeBase) -> None:
    assert len(kb.docs) >= 20
    assert len({c.chunk_id for c in kb.chunks}) == len(kb.chunks)
    assert all(c.text.strip() for c in kb.chunks)


def test_front_matter_is_required(tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text("## 제목\n본문", encoding="utf-8")
    with pytest.raises(KnowledgeFormatError):
        parse_doc(path)


def test_chunks_follow_headings(kb: KnowledgeBase) -> None:
    doc = kb.docs["return-policy"]
    headings = [c.heading for c in split_chunks(doc)]
    assert "반품 신청 기간" in headings and "단순변심 반품 배송비" in headings


def test_validity_label_separates_current_and_expired(kb: KnowledgeBase) -> None:
    today = date(2026, 11, 20)
    expired = kb.chunks_of("notice-2025-yearend-returns")[0]
    current = kb.chunks_of("return-policy")[0]
    limited = kb.chunks_of("notice-2026-winter-shipping")[0]
    assert expired.validity_label(today).startswith("적용 종료")
    assert current.validity_label(today).startswith("현행")
    assert limited.validity_label(today).startswith("기간 한정")
    assert not kb.docs["notice-2025-yearend-returns"].is_effective(today)


def test_tokenizer_keeps_domain_compounds() -> None:
    tokens = KiwiTokenizer().tokenize("무료배송으로 받았는데 반품비가 얼마인가요?")
    assert "무료배송" in tokens and "반품비" in tokens
    assert "는데" not in tokens and "가" not in tokens


@pytest.mark.parametrize(
    ("query", "expected_doc"),
    [
        ("반품하면 배송비 얼마 내요?", "return-policy"),
        ("카드로 결제했는데 환불 언제 들어와요", "refund-policy"),
        ("제주도 추가 배송비", "shipping-remote"),
        ("각인 텀블러 취소할 수 있나요", "custom-made"),
        ("과일이 상해서 왔어요", "fresh-food"),
        ("상담원 운영 시간", "cs-hours"),
    ],
)
def test_search_finds_the_policy_document(kb: KnowledgeBase, query: str, expected_doc: str) -> None:
    hits = kb.search(query, k=5)
    assert expected_doc in [h.chunk.doc_id for h in hits]


def test_search_modes_and_bounds(kb: KnowledgeBase) -> None:
    assert kb.search("", k=3) == []
    assert kb.search("반품", k=3, mode="bm25")
    assert kb.search("반품", k=3, mode="ngram")
    with pytest.raises(ValueError):
        kb.search("반품", k=0)


def test_rrf_prefers_documents_ranked_by_both() -> None:
    fused = rrf_fuse([[1, 2, 3], [3, 1, 4]])
    assert max(fused, key=fused.__getitem__) == 1
    assert fused[3] > fused[2] > fused[4]
