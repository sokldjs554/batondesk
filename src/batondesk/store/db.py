"""SQLite 저장소.

대화·메시지·턴 실행 기록·인계 티켓·운영 이벤트를 한 파일에 둔다. 공개 데모는 디스크가
재배포마다 초기화되는 환경이라, 시작할 때 저장된 상담 기록(data/recordings)을 다시 싣는다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    channel TEXT NOT NULL,
    sandbox_id TEXT NOT NULL,
    status TEXT NOT NULL,            -- open | handed_off | closed
    source TEXT NOT NULL,            -- live | rule-engine | recorded
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    pending_json TEXT NOT NULL DEFAULT '{}',
    resolution_json TEXT,
    feedback TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,              -- customer | ai | agent | system
    text TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (conversation_id, seq)
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    turn_seq INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    outcome TEXT NOT NULL,           -- answered | asked | handoff
    handoff_reason TEXT,
    handoff_source TEXT,
    grounding_verdict TEXT,
    revisions INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    engine TEXT NOT NULL,
    model TEXT,
    prompt_id TEXT,
    prompt_sha TEXT,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    tool_errors INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    kb_issue TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (conversation_id, turn_seq)
);
CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_seq INTEGER NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,            -- model | system | customer
    summary TEXT NOT NULL,
    status TEXT NOT NULL,            -- waiting | assigned | resolved
    created_at TEXT NOT NULL,
    assigned_to TEXT,
    assigned_at TEXT,
    resolved_at TEXT,
    resolution_note TEXT,
    snapshot_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    conversation_id TEXT,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS budget (
    day TEXT PRIMARY KEY,
    usd REAL NOT NULL DEFAULT 0,
    calls INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rate (
    key TEXT NOT NULL,
    hour TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key, hour)
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages (conversation_id, seq);
CREATE INDEX IF NOT EXISTS idx_turns_conv ON turns (conversation_id, turn_seq);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (type, ts);
CREATE INDEX IF NOT EXISTS idx_handoffs_status ON handoffs (status, created_at);
"""

JSON_COLUMNS = (
    "evidence_json",
    "pending_json",
    "resolution_json",
    "meta_json",
    "payload_json",
    "detail_json",
    "snapshot_json",
    "data_json",
)


def utcnow() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="milliseconds")


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out: dict[str, Any] = {}
    for key in row.keys():  # noqa: SIM118 - sqlite3.Row
        value = row[key]
        if key in JSON_COLUMNS:
            out[key.removesuffix("_json")] = json.loads(value) if value else None
        else:
            out[key] = value
    return out


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock:
            return _row(self._conn.execute(sql, params).fetchone())

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [r for r in (_row(row) for row in rows) if r is not None]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
