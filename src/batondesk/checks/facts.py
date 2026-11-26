"""답변 속 "굳은 사실"(금액·기간·날짜·비율·주문번호)을 근거와 글자 단위로 대조한다.

LLM 판정과 별개로 도는 규칙 검사다. 상담에서 가장 비싼 오류는 숫자가 틀리는 것이고
(반품 7일을 14일로, 6,000원을 3,000원으로), 숫자는 규칙으로 싸고 확실하게 잡을 수 있다.

한계(일부러 남긴 것): 근거의 값으로 계산해 나온 숫자("이틀 남았습니다")는 근거에 그 글자가
없으므로 "찾지 못함"으로 나온다. 그래서 이 검사 결과만으로 답변을 막지 않고, LLM 판정에
단서로 넘긴다. LLM 판정을 쓸 수 없을 때만 굳은 사실 불일치로 답변을 막는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_DURATIONS = {
    "일주일": "dur:7d",
    "하루": "dur:1d",
    "이틀": "dur:2d",
    "사흘": "dur:3d",
    "보름": "dur:15d",
    "한 달": "dur:1mo",
    "한달": "dur:1mo",
    "석 달": "dur:3mo",
    "반나절": "dur:12h",
}

_MONEY_MAN = re.compile(r"(\d+)\s*만\s*(?:(\d)\s*천)?\s*원")
_MONEY_CHEON = re.compile(r"(?<![\d만])(\d)\s*천\s*원")
_MONEY = re.compile(r"(\d[\d,]*)\s*원")
_BDAY_RANGE = re.compile(r"(\d+)\s*[~∼\-–]\s*(\d+)\s*영업일")
_BDAY = re.compile(r"(\d+)\s*영업일")
_DAY_RANGE = re.compile(r"(\d+)\s*[~∼\-–]\s*(\d+)\s*일(?!\s*[전후]?\s*영업)")
_DAY = re.compile(r"(?<![\d월\-])(\d+)\s*일(?![자요])")
_HOUR = re.compile(r"(\d+)\s*시간")
_MONTH = re.compile(r"(\d+)\s*개월")
_YEAR_SPAN = re.compile(r"(?<!\d)(\d{1,2})\s*년(?!\s*\d{1,2}\s*월)")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_KR_DATE = re.compile(r"(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|퍼센트)")
_CLOCK = re.compile(r"(오전|오후)\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?")
_ORDER = re.compile(r"BM-\d{5}", re.I)
_TRACKING = re.compile(r"HB\d{10}", re.I)
_JSON_INT = re.compile(r":\s*(\d{1,9})(?=\s*[,}\]])")
_CLOCK_24H = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)")

HARD_KINDS = ("amt", "pct", "date", "md", "order", "tracking", "clock")


@dataclass(frozen=True)
class FactFlag:
    fact: str  # 예: "amt:6000"
    kind: str
    surface: str  # 답변에 쓰인 표현
    hard: bool

    def as_dict(self) -> dict[str, object]:
        return {"fact": self.fact, "kind": self.kind, "surface": self.surface, "hard": self.hard}


def extract_facts(text: str, *, structured: bool = False) -> dict[str, str]:
    """text 에서 정규화한 사실 → 원문 표현. structured=True 는 JSON 도구 결과용(단위 없는 숫자도 금액 후보로 본다)."""
    found: dict[str, str] = {}

    def add(key: str, surface: str) -> None:
        found.setdefault(key, surface.strip())

    masked = text
    for m in _ISO_DATE.finditer(text):
        add(f"date:{m.group(1)}-{m.group(2)}-{m.group(3)}", m.group(0))
        add(f"md:{int(m.group(2)):02d}-{int(m.group(3)):02d}", m.group(0))
    for m in _KR_DATE.finditer(text):
        month, day = int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            add(f"md:{month:02d}-{day:02d}", m.group(0))
            if m.group(1):
                add(f"date:{m.group(1)}-{month:02d}-{day:02d}", m.group(0))
    masked = _ISO_DATE.sub(" ", masked)
    masked = _KR_DATE.sub(" ", masked)

    for m in _MONEY_MAN.finditer(masked):
        value = int(m.group(1)) * 10000 + (int(m.group(2)) * 1000 if m.group(2) else 0)
        add(f"amt:{value}", m.group(0))
    masked = _MONEY_MAN.sub(" ", masked)
    for m in _MONEY_CHEON.finditer(masked):
        add(f"amt:{int(m.group(1)) * 1000}", m.group(0))
    masked = _MONEY_CHEON.sub(" ", masked)
    for m in _MONEY.finditer(masked):
        add(f"amt:{int(m.group(1).replace(',', ''))}", m.group(0))
    masked = _MONEY.sub(" ", masked)

    for m in _BDAY_RANGE.finditer(masked):
        add(f"dur:{int(m.group(1))}bd", m.group(0))
        add(f"dur:{int(m.group(2))}bd", m.group(0))
    masked = _BDAY_RANGE.sub(" ", masked)
    for m in _BDAY.finditer(masked):
        add(f"dur:{int(m.group(1))}bd", m.group(0))
    masked = _BDAY.sub(" ", masked)
    for m in _DAY_RANGE.finditer(masked):
        add(f"dur:{int(m.group(1))}d", m.group(0))
        add(f"dur:{int(m.group(2))}d", m.group(0))
    masked = _DAY_RANGE.sub(" ", masked)
    for m in _DAY.finditer(masked):
        add(f"dur:{int(m.group(1))}d", m.group(0))
    for m in _HOUR.finditer(masked):
        add(f"dur:{int(m.group(1))}h", m.group(0))
    for m in _MONTH.finditer(masked):
        add(f"dur:{int(m.group(1))}mo", m.group(0))
    for m in _YEAR_SPAN.finditer(masked):
        add(f"dur:{int(m.group(1))}y", m.group(0))
    for word, key in _WORD_DURATIONS.items():
        if word in text:
            add(key, word)

    for m in _PCT.finditer(text):
        add(f"pct:{float(m.group(1)):g}", m.group(0))
    for m in _CLOCK.finditer(text):
        hour = int(m.group(2)) % 12 + (12 if m.group(1) == "오후" else 0)
        add(f"clock:{hour:02d}:{int(m.group(3) or 0):02d}", m.group(0))
    for m in _ORDER.finditer(text):
        add(f"order:{m.group(0).upper()}", m.group(0))
    for m in _TRACKING.finditer(text):
        add(f"tracking:{m.group(0).upper()}", m.group(0))

    for m in _CLOCK_24H.finditer(text):
        add(f"clock:{int(m.group(1)):02d}:{m.group(2)}", m.group(0))

    if structured:
        # 도구 결과(JSON)의 숫자 값은 단위가 없으므로 금액 후보로 본다. 0도 값이다("return_fee": 0 → "0원").
        for m in _JSON_INT.finditer(text):
            add(f"amt:{int(m.group(1))}", m.group(1))
    return found


def check_facts(answer: str, evidence_texts: list[tuple[str, bool]], extra_context: str = "") -> list[FactFlag]:
    """답변의 사실 중 근거(와 고객이 직접 말한 내용)에서 찾지 못한 것의 목록.

    evidence_texts: (본문, 도구 결과 JSON 여부)
    """
    known: set[str] = set(extract_facts(extra_context))
    for text, structured in evidence_texts:
        known.update(extract_facts(text, structured=structured))
    flags = []
    for fact, surface in extract_facts(answer).items():
        if fact in known:
            continue
        kind = fact.split(":", 1)[0]
        flags.append(FactFlag(fact=fact, kind=kind, surface=surface, hard=kind in HARD_KINDS))
    return flags
