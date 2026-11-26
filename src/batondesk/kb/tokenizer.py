"""BM25용 한국어 토크나이저.

출처: sokldjs554/kodoc-ai @ d7d332c `src/kodoc/rag/tokenizer.py` (같은 작성자의 저장소).
바꾼 점: 상담 도메인 복합어(반품비·무료배송·주문제작 등)를 사용자 사전으로 등록했다.
Kiwi 기본 분석은 "반품비"를 "반품"+"비"로 쪼개고, "비"처럼 한 글자가 된 토큰은
거의 모든 문서에 걸려 변별력을 잃는다.

한국어는 조사·어미가 어절에 붙기 때문에 공백 단위 토큰으로는 BM25가 제대로
동작하지 않는다 ("반품을" != "반품"). Kiwi 형태소 분석으로 내용어만 남긴다.
Kiwi가 설치되지 않은 환경에서는 정규식 폴백으로 동작을 보장한다.
"""

from __future__ import annotations

import re
from typing import Any, Protocol


class Tokenizer(Protocol):
    def tokenize(self, text: str) -> list[str]: ...


_WORD_RE = re.compile(r"[0-9A-Za-z가-힣]+")

# 상담 문의에 자주 나오지만 기본 분석에서 쪼개지는 복합어.
DOMAIN_WORDS: tuple[str, ...] = (
    "반품비",
    "배송비",
    "회수비",
    "무료배송",
    "주문제작",
    "도서산간",
    "적립금",
    "구매확정",
    "상품준비중",
    "결제완료",
    "배송중",
    "배송완료",
    "단순변심",
    "오배송",
    "초기불량",
    "신선식품",
    "설치배송",
    "부분환불",
    "무통장입금",
    "간편결제",
    "비회원",
    "상담원",
    "재입고",
    "청약철회",
)


class RegexTokenizer:
    """형태소 분석기가 없을 때의 최소 폴백. 어절 단위 분리만 수행한다."""

    def tokenize(self, text: str) -> list[str]:
        return [t.lower() for t in _WORD_RE.findall(text)]


class KiwiTokenizer:
    """Kiwi 형태소 분석 기반 토크나이저.

    조사(J*)·어미(E*)·기호를 걸러내고 내용어만 남긴다:
    체언(NN*, NR, NP), 용언 어간(VV, VA), 어근(XR), 외국어/숫자(SL, SN, SH).
    """

    _KEEP_PREFIX = ("NN", "NR", "NP", "VV", "VA", "XR", "SL", "SN", "SH")
    _shared_kiwi: Any = None  # Kiwi 모델 로딩(~1초)은 프로세스당 1회면 충분하다

    def __init__(self) -> None:
        from kiwipiepy import Kiwi

        if KiwiTokenizer._shared_kiwi is None:
            kiwi = Kiwi()
            for word in DOMAIN_WORDS:
                kiwi.add_user_word(word, "NNG", 5.0)
            KiwiTokenizer._shared_kiwi = kiwi
        self._kiwi = KiwiTokenizer._shared_kiwi

    def tokenize(self, text: str) -> list[str]:
        return [
            token.form.lower()
            for token in self._kiwi.tokenize(text)
            if token.tag.startswith(self._KEEP_PREFIX)
        ]


def get_tokenizer() -> Tokenizer:
    """가능하면 Kiwi, 아니면 정규식 폴백을 돌려준다."""
    try:
        return KiwiTokenizer()
    except ImportError:
        return RegexTokenizer()
