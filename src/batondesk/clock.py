"""기준 시각.

예시 주문의 날짜는 "며칠 전"으로 적혀 있고 기준 시각에 맞춰 실제 날짜로 풀린다.
평가는 기준 시각을 고정해 같은 질문이 언제 실행해도 같은 정답을 갖게 한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), name="KST")


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)
