"""예시 주문 시스템 HTTP 클라이언트.

- 제한 시간: 전송 계층과 상관없이 호출 전체에 asyncio 제한 시간을 건다.
- 재시도: 읽기 호출만, 5xx·시간 초과에 한해 정해진 횟수만큼 다시 시도한다.
  쓰기 호출은 재시도하지 않는다 — 시간 초과는 "실행되지 않았다"는 뜻이 아니기 때문이다.
- 실패는 예외가 아니라 결과 값으로 돌려준다. 에이전트가 실패를 보고 판단해야 하기 때문이다.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

OutcomeStatus = Literal["ok", "not_found", "rejected", "unavailable", "timeout"]


@dataclass
class CallOutcome:
    status: OutcomeStatus
    data: dict[str, Any] = field(default_factory=dict)
    http_status: int | None = None
    attempts: int = 1
    latency_ms: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class CommerceClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout_s: float = 2.5,
        retries: int = 1,
    ) -> None:
        self._http = http
        self.timeout_s = timeout_s
        self.retries = retries

    async def call(
        self,
        method: str,
        path: str,
        *,
        sandbox_id: str,
        customer_id: str | None = None,
        json_body: dict[str, Any] | None = None,
        idempotent: bool = True,
    ) -> CallOutcome:
        headers = {"X-Sandbox-Id": sandbox_id}
        if customer_id:
            headers["X-Customer-Id"] = customer_id
        max_attempts = 1 + (self.retries if idempotent else 0)
        started = time.perf_counter()
        last: CallOutcome | None = None
        for attempt in range(1, max_attempts + 1):
            last = await self._once(method, path, headers, json_body)
            last.attempts = attempt
            if last.status in ("ok", "not_found", "rejected"):
                break
            if attempt < max_attempts:
                await asyncio.sleep(0.15 + random.random() * 0.15)
        assert last is not None
        last.latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return last

    async def _once(
        self, method: str, path: str, headers: dict[str, str], json_body: dict[str, Any] | None
    ) -> CallOutcome:
        try:
            async with asyncio.timeout(self.timeout_s):
                resp = await self._http.request(method, path, headers=headers, json=json_body)
        except TimeoutError:
            return CallOutcome("timeout", error=f"{self.timeout_s}초 안에 응답이 없습니다")
        except httpx.HTTPError as exc:
            return CallOutcome("unavailable", error=f"{type(exc).__name__}")
        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        if resp.status_code == 200:
            return CallOutcome("ok", data=payload, http_status=200)
        detail = payload.get("detail") if isinstance(payload, dict) else None
        message = detail.get("message") if isinstance(detail, dict) else str(detail or "")
        if resp.status_code == 404:
            return CallOutcome("not_found", http_status=404, error=message)
        if 400 <= resp.status_code < 500:
            return CallOutcome("rejected", http_status=resp.status_code, error=message)
        return CallOutcome("unavailable", http_status=resp.status_code, error=message)
