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

import yaml

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
    def __init__(
        self,
        docs: list[KnowledgeDoc],
        tokenizer: Tokenizer | None = None,
        rrf_k: int = 60,
        synonyms: list[tuple[tuple[str, ...], str]] | None = None,
    ) -> None:
        self.docs = {d.doc_id: d for d in docs}
        self.chunks: list[Chunk] = [c for d in docs for c in split_chunks(d)]
        self.tokenizer = tokenizer or get_tokenizer()
        self.rrf_k = rrf_k
        texts = [_index_text(c) for c in self.chunks]
        self._bm25 = BM25([self.tokenizer.tokenize(t) for t in texts])
        self._ngram = CharNgramIndex(texts)
        self._by_id = {c.chunk_id: c for c in self.chunks}
        self.synonyms = synonyms or []

    @classmethod
    def from_dir(cls, kb_dir: Path, tokenizer: Tokenizer | None = None) -> KnowledgeBase:
        return cls(load_docs(kb_dir), tokenizer=tokenizer, synonyms=load_synonyms(kb_dir / "synonyms.yaml"))

    def expand(self, query: str) -> str:
        """고객 표현을 문서 표현으로 이어 주는 단어를 질의 뒤에 붙인다."""
        added = [add for triggers, add in self.synonyms if any(t in query for t in triggers)]
        return f"{query} {' '.join(added)}" if added else query

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        return self._by_id.get(chunk_id)

    def chunks_of(self, doc_id: str) -> list[Chunk]:
        return [c for c in self.chunks if c.doc_id == doc_id]

    def search(self, query: str, k: int = 5, mode: SearchMode = "hybrid", *, expand: bool = True) -> list[SearchHit]:
        """mode 와 expand 는 평가·디버깅을 위해 따로 끌 수 있게 노출한다."""
        if k < 1:
            raise ValueError(f"k는 1 이상이어야 합니다: {k}")
        if not self.chunks or not query.strip():
            return []
        if expand:
            query = self.expand(query)
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


def load_synonyms(path: Path) -> list[tuple[tuple[str, ...], str]]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [(tuple(str(t) for t in item["when"]), str(item["add"])) for item in raw]
