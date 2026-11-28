"""전달 응답 지표의 단위와 출처별 모집단을 손으로 센 작은 저장소로 확인한다."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from batondesk.obs.metrics import compute_metrics
from batondesk.store.db import Database
from batondesk.store.repo import Repo


@pytest.fixture
def response_repo() -> Iterator[Repo]:
    db = Database(":memory:")
    repo = Repo(db)
    # live: 다회차 대화 1개(저장된 AI 메시지 3개), 직접 인계 1개, 빈 대화 1개.
    # recorded: 답변한 대화 2개(AI 메시지 각 1개), 빈 대화 1개.
    # 턴 수와 메시지 수를 일부러 다르게 둔다. 실행 기록이나 turn_count로 추정하면 틀린다.
    for cid, source, turn_count, roles in (
        ("live_multi", "live", 2, ("customer", "ai", "system", "agent", "ai", "customer", "ai")),
        ("live_direct", "live", 0, ("customer", "system", "agent")),
        ("live_empty", "live", 0, ()),
        ("recorded_one", "recorded", 1, ("customer", "ai", "agent")),
        ("recorded_two", "recorded", 1, ("customer", "ai", "system")),
        ("recorded_empty", "recorded", 0, ()),
    ):
        repo.create_conversation(
            conversation_id=cid, customer_id="C-1001", customer_name="예시 고객", channel="web",
            sandbox_id="test", source=source,
        )
        repo.update_conversation(cid, turn_count=turn_count)
        for role in roles:
            repo.add_message(cid, role, f"{role}의 저장된 메시지")
    repo.create_handoff(
        conversation_id="live_direct", turn_seq=0, reason="customer_request", source="customer",
        summary="고객이 직접 인계 요청", snapshot={},
    )
    yield repo
    db.close()


@pytest.mark.parametrize(
    ("source", "total", "expected"),
    [
        (None, 6, {"delivered": 5, "denominator": 4, "per_conversation": 1.25}),
        ("live", 3, {"delivered": 3, "denominator": 2, "per_conversation": 1.5}),
        ("recorded", 3, {"delivered": 2, "denominator": 2, "per_conversation": 1.0}),
        ("rule-engine", 0, {"delivered": 0, "denominator": 0, "per_conversation": None}),
    ],
)
def test_response_metric_counts_saved_ai_messages_in_the_selected_source(
    response_repo: Repo, source: str | None, total: int, expected: dict[str, Any],
) -> None:
    metrics = compute_metrics(response_repo, source)
    assert metrics["conversations"]["total"] == total
    assert metrics["conversations"]["ai_involved"] == expected["denominator"]
    assert metrics["responses"] == expected
