"""답변 속 "굳은 사실"(금액·기간·날짜·비율·주문번호)을 근거와 글자 단위로 대조한다.

LLM 판정과 별개로 도는 규칙 검사다. 상담에서 가장 비싼 오류는 숫자가 틀리는 것이고
(반품 7일을 14일로, 6,000원을 3,000원으로), 숫자는 규칙으로 싸고 확실하게 잡을 수 있다.

근거에 없는 기간도 기본적으로 굳은 사실 불일치다. 다만 "이틀 남았습니다"처럼 각 등장
위치에서 남은/지난 기간으로 읽히는 값은 계산 여부를 LLM이 확인하도록 약한 단서로 남긴다.
"예정일은 20일"처럼 날짜로도 읽히는 값도 약한 단서다. 계산·생략된 월/년을 검증하는 규칙은
아니다. LLM 판정을 쓸 수 없을 때 굳은 사실 불일치로 막는다.
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
_BDAY_RANGE = re.compile(r"(\d+)\s*(?:영업일\s*)?[~∼\-–]\s*(\d+)\s*영업일")
_BDAY = re.compile(r"(\d+)\s*영업일")
_DAY_RANGE = re.compile(r"(\d+)\s*(?:일\s*)?[~∼\-–]\s*(\d+)\s*일(?!\s*[전후]?\s*영업)")
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
_DERIVED_DURATION_AFTER = re.compile(
    r"\s*(?:(?:정도|가량|쯤)\s*)?(?:(?:이|가|은|는|만|밖에)\s*)?(?:더\s*)?"
    r"(?:남(?:았|아|은|는|게|습니다|지)|지(?:났|난|나)|경과)"
)
_DERIVED_DURATION_BEFORE = re.compile(r"(?:남은|경과한|지난)\s*(?:(?:기간|시간|기한|일수)(?:은|는|이|가)?\s*)?(?:약\s*)?$")
_DURATION_LIMIT_AFTER = re.compile(r"\s*(?:이내|안에|내에)")
_DURATION_CONDITION_AFTER = re.compile(
    r"\s*(?:으?면|고\s*나면|게\s*되면|(?:하|했|되|됐)으?면|(?:하는|한|할|했을|되는|된|될|을)?\s*(?:경우|때|시))"
)
_CALENDAR_DAY_BEFORE = re.compile(r"(?:예정일|배송일|도착일|수령일|환불일|마감일|날짜)(?:은|는|이|가)?\s*[:：]?\s*$")
_CALENDAR_DAY_AFTER = re.compile(r"\s*(?:입니다|이에요|이고|이며|[.,!?;]|$)")

# 기간은 같은 값도 등장 위치에 따라 계산값/정책값이 다를 수 있으므로 별도로 판별한다.
HARD_KINDS = ("amt", "pct", "date", "md", "order", "tracking", "clock")


@dataclass(frozen=True)
class FactFlag:
    fact: str  # 예: "amt:6000"
    kind: str
    surface: str  # 답변에 쓰인 표현
    hard: bool

    def as_dict(self) -> dict[str, object]:
        return {"fact": self.fact, "kind": self.kind, "surface": self.surface, "hard": self.hard}


def _duration_mentions(text: str) -> list[tuple[str, re.Match[str]]]:
    """정규화한 기간과 모든 등장 위치. 날짜/범위 마스킹은 원문의 위치를 보존한다."""
    found: list[tuple[str, re.Match[str]]] = []
    masked = text
    for pattern in (_ISO_DATE, _KR_DATE):
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    for pattern, unit, groups in (
        (_BDAY_RANGE, "bd", (1, 2)),
        (_BDAY, "bd", (1,)),
        (_DAY_RANGE, "d", (1, 2)),
        (_DAY, "d", (1,)),
        (_HOUR, "h", (1,)),
        (_MONTH, "mo", (1,)),
        (_YEAR_SPAN, "y", (1,)),
    ):
        for m in pattern.finditer(masked):
            found.extend((f"dur:{int(m.group(group))}{unit}", m) for group in groups)
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    for word, key in _WORD_DURATIONS.items():
        found.extend((key, m) for m in re.finditer(re.escape(word), masked))
    return found


def _is_hard_duration(text: str, mention: re.Match[str]) -> bool:
    """계산 사실과 조건부 정책을 구분하고, 명시적 날짜 문맥의 짧은 일자는 보류한다."""
    after = _DERIVED_DURATION_AFTER.match(text, mention.end())
    if _DURATION_LIMIT_AFTER.match(text, mention.end()) or (after and _DURATION_CONDITION_AFTER.match(text, after.end())):
        return True
    day = _DAY.fullmatch(mention.group(0))
    if (
        day
        and 1 <= int(day.group(1)) <= 31
        and _CALENDAR_DAY_BEFORE.search(text[: mention.start()])
        and _CALENDAR_DAY_AFTER.match(text, mention.end())
    ):
        # "20일"을 근거의 어느 월/년과 묶을지 추측하지 않는다. 약한 단서는 계속 남긴다.
        return False
    return not (after or _DERIVED_DURATION_BEFORE.search(text[: mention.start()]))


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

    for key, m in _duration_mentions(text):
        add(key, m.group(0))

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
    # 중복 값을 합치기 전에 매 등장 위치를 본다. "14일 남음"이 별도의 "14일 이내"를 면제하지 않는다.
    hard_durations = {fact: m.group(0) for fact, m in _duration_mentions(answer) if _is_hard_duration(answer, m)}
    flags = []
    for fact, surface in extract_facts(answer).items():
        if fact in known:
            continue
        kind = fact.split(":", 1)[0]
        flags.append(
            FactFlag(fact=fact, kind=kind, surface=hard_durations.get(fact, surface), hard=kind in HARD_KINDS or fact in hard_durations)
        )
    return flags
