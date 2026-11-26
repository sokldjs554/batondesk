"""문자 n-gram TF-IDF 랭커.

형태소 BM25는 정확 매칭에 강하지만 분석이 어긋나면("환불받다" vs "환불") 놓친다.
문자 2~3-gram 코사인은 띄어쓰기·활용형·오타에 덜 민감해서 두 번째 랭커로 쓴다.

임베딩(dense) 랭커를 쓰지 않은 이유는 docs/design-decisions.md 의 "검색" 항목에 있다.
요약: 사용 중인 LLM 공급자가 임베딩 API를 제공하지 않고, 무료 호스팅(512MB)에
로컬 임베딩 모델을 올리는 비용이 문서 20여 건 규모의 이득보다 크다고 판단했다.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_KEEP = re.compile(r"[0-9a-z가-힣]+")


def _ngrams(text: str, sizes: tuple[int, ...] = (2, 3)) -> list[str]:
    grams: list[str] = []
    for word in _KEEP.findall(text.lower()):
        for n in sizes:
            if len(word) < n:
                continue
            grams.extend(word[i : i + n] for i in range(len(word) - n + 1))
    return grams


class CharNgramIndex:
    def __init__(self, corpus: list[str]) -> None:
        self.n_docs = len(corpus)
        counts = [Counter(_ngrams(text)) for text in corpus]
        df: Counter[str] = Counter()
        for c in counts:
            df.update(c.keys())
        self.idf = {g: math.log((1 + self.n_docs) / (1 + d)) + 1.0 for g, d in df.items()}
        self.vectors: list[dict[str, float]] = []
        self.postings: dict[str, list[int]] = {}
        for doc_id, c in enumerate(counts):
            vec = {g: (1.0 + math.log(tf)) * self.idf[g] for g, tf in c.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            self.vectors.append({g: v / norm for g, v in vec.items()})
            for g in c:
                self.postings.setdefault(g, []).append(doc_id)

    def top_n(self, query: str, n: int) -> list[tuple[int, float]]:
        q = Counter(_ngrams(query))
        qvec = {g: (1.0 + math.log(tf)) * self.idf[g] for g, tf in q.items() if g in self.idf}
        qnorm = math.sqrt(sum(v * v for v in qvec.values()))
        if not qnorm:
            return []
        scores: dict[int, float] = {}
        for g, qv in qvec.items():
            for doc_id in self.postings[g]:
                scores[doc_id] = scores.get(doc_id, 0.0) + (qv / qnorm) * self.vectors[doc_id][g]
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:n]
