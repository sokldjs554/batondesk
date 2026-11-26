"""지식 검색기: 형태소 BM25 + 문자 n-gram, RRF 융합.

뼈대 출처: sokldjs554/kodoc-ai @ d7d332c `src/kodoc/rag/retriever.py` (같은 작성자의 저장소).
바꾼 점:
- dense(임베딩) 랭커를 문자 n-gram TF-IDF 랭커로 교체했다(이유는 charngram.py).
- 색인은 시작할 때 한 번 만들고 바꾸지 않으므로 증분 추가·영속화·락을 뺐다.
- 청크에 문서 종류와 적용 기간을 실어, 답변이 "현행 정책"과 "끝난 공지"를 구분할 수 있게 했다.

RRF: Cormack, Clarke & Buettcher (SIGIR 2009). 점수 스케일이 다른 두 랭커를 순위만으로 합친다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from batondesk.kb.bm25 import BM25
from batondesk.kb.charngram import CharNgramIndex
from batondesk.kb.loader import load_docs, split_chunks
from batondesk.kb.models import Chunk, KnowledgeDoc, SearchHit
from batondesk.kb.tokenizer import Tokenizer, get_tokenizer

SearchMode = Literal["hybrid", "bm25", "ngram"]

# 각 랭커에서 최종 k의 몇 배수까지 후보를 볼지.
CANDIDATE_MULTIPLIER = 4


def rrf_fuse(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    """Reciprocal Rank Fusion. score(d) = Σ 1 / (k + rank_i(d)), rank는 1부터."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def _index_text(chunk: Chunk) -> str:
    # 제목과 절 제목은 질의와 겹칠 가능성이 높아 한 번 더 넣어 가중한다.
    return f"{chunk.title} {chunk.heading} {chunk.title} {chunk.heading} {chunk.text}"


class KnowledgeBase:
    def __init__(self, docs: list[KnowledgeDoc], tokenizer: Tokenizer | None = None, rrf_k: int = 60) -> None:
        self.docs = {d.doc_id: d for d in docs}
        self.chunks: list[Chunk] = [c for d in docs for c in split_chunks(d)]
        self.tokenizer = tokenizer or get_tokenizer()
        self.rrf_k = rrf_k
        texts = [_index_text(c) for c in self.chunks]
        self._bm25 = BM25([self.tokenizer.tokenize(t) for t in texts])
        self._ngram = CharNgramIndex(texts)
        self._by_id = {c.chunk_id: c for c in self.chunks}

    @classmethod
    def from_dir(cls, kb_dir: Path, tokenizer: Tokenizer | None = None) -> KnowledgeBase:
        return cls(load_docs(kb_dir), tokenizer=tokenizer)

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)

    def chunks_of(self, doc_id: str) -> list[Chunk]:
        return [c for c in self.chunks if c.doc_id == doc_id]

    def search(self, query: str, k: int = 5, mode: SearchMode = "hybrid") -> list[SearchHit]:
        """mode 는 평가·디버깅을 위해 단일 랭커도 노출한다."""
        if k < 1:
            raise ValueError(f"k는 1 이상이어야 합니다: {k}")
        if not self.chunks or not query.strip():
            return []
        k = min(k, len(self.chunks))
        depth = k * CANDIDATE_MULTIPLIER

        bm25_ranked = self._bm25.top_n(self.tokenizer.tokenize(query), depth)
        ngram_ranked = self._ngram.top_n(query, depth)
        bm25_ids = [i for i, _ in bm25_ranked]
        ngram_ids = [i for i, _ in ngram_ranked]

        if mode == "bm25":
            fused = rrf_fuse([bm25_ids], k=self.rrf_k)
        elif mode == "ngram":
            fused = rrf_fuse([ngram_ids], k=self.rrf_k)
        else:
            fused = rrf_fuse([bm25_ids, ngram_ids], k=self.rrf_k)

        bm25_rank = {i: r for r, i in enumerate(bm25_ids, start=1)}
        ngram_rank = {i: r for r, i in enumerate(ngram_ids, start=1)}
        bm25_score = dict(bm25_ranked)
        top = sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:k]
        return [
            SearchHit(
                chunk=self.chunks[i],
                score=score,
                bm25_rank=bm25_rank.get(i),
                ngram_rank=ngram_rank.get(i),
                bm25_score=bm25_score.get(i),
            )
            for i, score in top
        ]
