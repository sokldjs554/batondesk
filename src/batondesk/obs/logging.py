"""구조화 로그.

운영 이벤트를 한 줄 JSON으로 표준 출력에 쓴다. 호스팅 서비스의 로그 화면에서 그대로 검색할 수 있고,
같은 이벤트가 SQLite events 테이블에도 들어가 운영 화면의 집계에 쓰인다.

고객 메시지 본문은 로그에 쓰지 않는다. 이벤트에는 길이·ID·상태·지연 시간 같은 메타 정보만 싣는다
(지식 검색 질의는 검색 품질을 보려면 필요해서 예외로 남긴다 — 예시 데이터라 가능한 선택이다).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_logger = logging.getLogger("batondesk.events")


def configure_logging(level: int = logging.INFO) -> None:
    if _logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(level)
    _logger.propagate = False


def log_event(event: str, **fields: Any) -> None:
    record = {"ts": datetime.now(tz=UTC).isoformat(timespec="milliseconds"), "event": event, **fields}
    _logger.info(json.dumps(record, ensure_ascii=False, default=str))
