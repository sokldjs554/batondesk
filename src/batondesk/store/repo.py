"""저장소 접근 함수 모음. SQL은 여기에만 둔다."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from batondesk.store.db import Database, dumps, utcnow


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class Repo:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ---- 대화 -----------------------------------------------------------------

    def create_conversation(
        self,
        *,
        customer_id: str,
        customer_name: str,
        channel: str,
        sandbox_id: str,
        source: str,
        meta: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        cid = conversation_id or new_id("conv")
        now = created_at or utcnow()
        self.db.execute(
            "INSERT INTO conversations (id, customer_id, customer_name, channel, sandbox_id, status, source, created_at, updated_at, meta_json)"
            " VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)",
            (cid, customer_id, customer_name, channel, sandbox_id, source, now, now, dumps(meta or {})),
        )
        conv = self.get_conversation(cid)
        assert conv is not None
        return conv

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return self.db.one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))

    def find_conversation_by_meta(self, channel: str, external_key: str) -> dict[str, Any] | None:
        return self.db.one(
            "SELECT * FROM conversations WHERE channel = ? AND json_extract(meta_json, '$.external_key') = ?"
            " AND status != 'closed' ORDER BY created_at DESC LIMIT 1",
            (channel, external_key),
        )

    def list_conversations(self, *, status: str | None = None, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if source:
            where.append("source = ?")
            params.append(source)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        return self.db.all(f"SELECT * FROM conversations {clause} ORDER BY updated_at DESC LIMIT ?", (*params, limit))

    def update_conversation(self, conversation_id: str, **fields: Any) -> None:
        if not fields:
            return
        columns, params = [], []
        for key, value in fields.items():
            if key in ("evidence", "pending", "resolution", "meta"):
                columns.append(f"{key}_json = ?")
                params.append(dumps(value) if value is not None else None)
            else:
                columns.append(f"{key} = ?")
                params.append(value)
        columns.append("updated_at = ?")
        params.append(utcnow())
        self.db.execute(f"UPDATE conversations SET {', '.join(columns)} WHERE id = ?", (*params, conversation_id))

    # ---- 메시지 ---------------------------------------------------------------

    def add_message(
        self, conversation_id: str, role: str, text: str, payload: dict[str, Any] | None = None, created_at: str | None = None
    ) -> dict[str, Any]:
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM messages WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
            seq = int(row["next"])
            now = created_at or utcnow()
            conn.execute(
                "INSERT INTO messages (conversation_id, seq, role, text, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, seq, role, text, dumps(payload or {}), now),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
        return {"seq": seq, "role": role, "text": text, "payload": payload or {}, "created_at": now}

    def messages(self, conversation_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        return self.db.all(
            "SELECT seq, role, text, payload_json, created_at FROM messages WHERE conversation_id = ? AND seq > ? ORDER BY seq",
            (conversation_id, after_seq),
        )

    # ---- 턴 실행 기록 ----------------------------------------------------------

    def add_turn(self, conversation_id: str, turn_seq: int, record: dict[str, Any]) -> None:
        columns = (
            "started_at", "latency_ms", "outcome", "handoff_reason", "handoff_source", "grounding_verdict",
            "revisions", "blocked_count", "engine", "model", "prompt_id", "prompt_sha", "llm_calls",
            "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "tool_calls", "tool_errors",
            "error", "kb_issue",
        )  # fmt: skip
        values = [record.get(c) for c in columns]
        self.db.execute(
            f"INSERT INTO turns (conversation_id, turn_seq, {', '.join(columns)}, detail_json)"
            f" VALUES (?, ?, {', '.join('?' for _ in columns)}, ?)",
            (conversation_id, turn_seq, *values, dumps(record.get("detail", {}))),
        )

    def turns(self, conversation_id: str) -> list[dict[str, Any]]:
        return self.db.all("SELECT * FROM turns WHERE conversation_id = ? ORDER BY turn_seq", (conversation_id,))

    # ---- 인계 -----------------------------------------------------------------

    def create_handoff(
        self,
        *,
        conversation_id: str,
        turn_seq: int,
        reason: str,
        source: str,
        summary: str,
        snapshot: dict[str, Any],
        handoff_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        hid = handoff_id or new_id("ho")
        self.db.execute(
            "INSERT INTO handoffs (id, conversation_id, turn_seq, reason, source, summary, status, created_at, snapshot_json)"
            " VALUES (?, ?, ?, ?, ?, ?, 'waiting', ?, ?)",
            (hid, conversation_id, turn_seq, reason, source, summary, created_at or utcnow(), dumps(snapshot)),
        )
        handoff = self.get_handoff(hid)
        assert handoff is not None
        return handoff

    def get_handoff(self, handoff_id: str) -> dict[str, Any] | None:
        return self.db.one("SELECT * FROM handoffs WHERE id = ?", (handoff_id,))

    def open_handoff_for(self, conversation_id: str) -> dict[str, Any] | None:
        return self.db.one(
            "SELECT * FROM handoffs WHERE conversation_id = ? AND status != 'resolved' ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        )

    def latest_handoff_for(self, conversation_id: str) -> dict[str, Any] | None:
        return self.db.one("SELECT * FROM handoffs WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 1", (conversation_id,))

    def list_handoffs(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if status:
            return self.db.all("SELECT * FROM handoffs WHERE status = ? ORDER BY created_at DESC LIMIT ?", (status, limit))
        return self.db.all("SELECT * FROM handoffs ORDER BY created_at DESC LIMIT ?", (limit,))

    def update_handoff(self, handoff_id: str, **fields: Any) -> None:
        columns = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE handoffs SET {columns} WHERE id = ?", (*fields.values(), handoff_id))

    # ---- 이벤트·한도 -----------------------------------------------------------

    def add_event(self, type_: str, conversation_id: str | None, data: dict[str, Any], ts: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO events (ts, type, conversation_id, data_json) VALUES (?, ?, ?, ?)",
            (ts or utcnow(), type_, conversation_id, dumps(data)),
        )

    def events(self, type_: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if type_:
            return self.db.all("SELECT * FROM events WHERE type = ? ORDER BY id DESC LIMIT ?", (type_, limit))
        return self.db.all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    def spend_today(self) -> dict[str, Any]:
        day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        row = self.db.one("SELECT usd, calls FROM budget WHERE day = ?", (day,))
        return {"day": day, "usd": float(row["usd"]) if row else 0.0, "calls": int(row["calls"]) if row else 0}

    def add_spend(self, usd: float) -> None:
        day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        self.db.execute(
            "INSERT INTO budget (day, usd, calls) VALUES (?, ?, 1)"
            " ON CONFLICT(day) DO UPDATE SET usd = usd + excluded.usd, calls = calls + 1",
            (day, usd),
        )

    def hit_rate(self, key: str) -> int:
        """이번 시간대의 호출 수를 1 올리고 올린 뒤의 값을 돌려준다."""
        hour = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H")
        with self.db.tx() as conn:
            conn.execute(
                "INSERT INTO rate (key, hour, count) VALUES (?, ?, 1) ON CONFLICT(key, hour) DO UPDATE SET count = count + 1",
                (key, hour),
            )
            row = conn.execute("SELECT count FROM rate WHERE key = ? AND hour = ?", (key, hour)).fetchone()
        return int(row["count"])
