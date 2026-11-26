"""모델 호출 비용 추정.

단가는 2026-11-03 시점 Anthropic 공개 단가를 옮겨 적은 값이다(USD / 100만 토큰).
청구서가 아니라 응답의 usage 토큰 수에 단가를 곱한 추정치이며, 단가가 바뀌면 이 표를 고쳐야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": Price(2.00, 10.00, 0.10, 2.50),
    "claude-opus-5-5": Price(4.00, 20.00, 0.20, 5.00),
}


def estimate_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    total = input_tokens * price.input + output_tokens * price.output + cache_read * price.cache_read + cache_write * price.cache_write
    return round(total / 1_000_000, 6)
