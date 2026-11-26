"""구성 요소 평가 자료(data/evals/{grounding,resolution,retrieval}/{dev,test}.jsonl) 검사.

평가 숫자는 자료가 멀쩡할 때만 뜻이 있다. 실제 모델을 돌리기 전에, 그리고 CI에서 매번 다음을 확인한다.

- 형식: JSON 한 줄에 한 건, 정해진 필드만, ID 중복 없음, ID 접두사와 split 이 파일과 맞음.
- 라벨·종류가 허용된 값인지, 종류가 라벨과 맞는지.
- dev 와 test 사이에 같은 문항이나 거의 같은 문항이 없는지(정규화한 글자열 일치, 문자 3-gram 자카드 > 0.8).
- 근거 일치 자료: 근거가 진짜인지(지식 문서 근거는 kb/ 의 청크 본문과 글자 그대로 같아야 하고, 도구 근거는 JSON 이어야 한다),
  ungrounded 문항의 wrong_span 이 답변 안에 실제로 있는지, grounded 문항에는 wrong_span 이 없는지.
- 검색 자료: 정답 문서·청크 ID가 실제로 있는지.

검색 자료의 정답 읽는 법: kind 가 multi_doc 이면 gold_chunks 가 "모두" 있어야 답할 수 있다는 뜻이고,
그 밖의 kind 는 gold_chunks 중 "하나"만 있어도 답할 수 있다는 뜻이다.

쓰는 법:
    python scripts/validate_eval_data.py            # 문제가 있으면 종료 코드 1
    from validate_eval_data import validate         # pytest 등에서: 문제 목록(빈 목록이면 통과)
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from batondesk.kb.loader import load_docs, split_chunks

REPO_ROOT = Path(__file__).resolve().parents[1]
SPLITS = ("dev", "test")
NEAR_DUPLICATE_JACCARD = 0.8

GROUNDED_KINDS = (
    "faithful",
    "paraphrase",
    "computed",
    "multi_evidence",
    "negative",
    "expired_handled",
    # 실제 에이전트 출력에서 판정기가 잘못 막던 유형(2026-11-26 추가)
    "process_statement",
    "session_context",
)
UNGROUNDED_KINDS = (
    "number_swap",
    "period_swap",
    "negation_flip",
    "invented_policy",
    "unsupported_promise",
    "expired_applied",
    "wrong_order_fact",
    "partial",
    # 상담 진행 발화처럼 보이지만 결과·혜택·시한을 약속하는 말, 시스템이 확정하지 않은 값을 확정처럼 말하는 답(2026-11-26 추가)
    "outcome_promise",
    "unconfirmed_as_confirmed",
)
RESOLUTION_LABELS = ("resolved_confirmed", "resolved_assumed", "unresolved", "pending_customer", "not_applicable")
RETRIEVAL_KINDS = ("direct", "paraphrase", "colloquial", "typo_spacing", "multi_doc")
ROLES = ("고객", "AI", "상담원")

FIELDS: dict[str, tuple[str, ...]] = {
    "grounding": ("id", "split", "label", "kind", "evidence", "conversation", "answer", "wrong_span", "note"),
    "resolution": ("id", "split", "label", "messages", "kind", "note"),
    "retrieval": ("id", "split", "query", "gold_docs", "gold_chunks", "kind", "note"),
}
# 있어도 되고 없어도 되는 필드. customer_name 은 답변이 고객 이름을 부를 때 판정기에 함께 주는 상담 정보다.
OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {"grounding": ("customer_name",)}
ID_PREFIX = {"grounding": "G", "resolution": "R", "retrieval": "S"}
EVIDENCE_FIELDS = ("id", "kind", "title", "text")

_NORMALIZE = re.compile(r"[^0-9a-z가-힣]+")


def _normalize(text: str) -> str:
    return _NORMALIZE.sub("", text.lower())


def _trigrams(text: str) -> set[str]:
    norm = _normalize(text)
    return {norm[i : i + 3] for i in range(len(norm) - 2)} if len(norm) >= 3 else {norm}


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def _load(path: Path, problems: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        problems.append(f"{path}: 파일이 없습니다")
        return []
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            problems.append(f"{path}:{number}: 빈 줄")
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            problems.append(f"{path}:{number}: JSON 이 아닙니다 ({exc})")
            continue
        if not isinstance(row, dict):
            problems.append(f"{path}:{number}: 객체가 아닙니다")
            continue
        rows.append(row)
    if not rows:
        problems.append(f"{path}: 문항이 한 건도 없습니다")
    return rows


def _check_common(name: str, split: str, rows: list[dict[str, Any]], seen: set[str], problems: list[str]) -> list[dict[str, Any]]:
    """필드·ID·split 검사. 뒤 검사에 쓸 수 있는(필드가 온전한) 행만 돌려준다."""
    expected = set(FIELDS[name])
    pattern = re.compile(rf"^{ID_PREFIX[name]}-{split[0].upper()}-\d{{3}}$")
    usable = []
    for index, row in enumerate(rows, start=1):
        where = f"{name}/{split}.jsonl #{index} ({row.get('id', '?')})"
        missing, unknown = expected - set(row), set(row) - expected - set(OPTIONAL_FIELDS.get(name, ()))
        if missing:
            problems.append(f"{where}: 빠진 필드 {sorted(missing)}")
        if unknown:
            problems.append(f"{where}: 모르는 필드 {sorted(unknown)}")
        if missing or unknown:
            continue
        rid = row["id"]
        if not isinstance(rid, str) or not pattern.match(rid):
            problems.append(f"{where}: ID 형식이 {pattern.pattern} 이 아닙니다")
        elif rid in seen:
            problems.append(f"{where}: 중복된 ID")
        else:
            seen.add(rid)
        if row["split"] != split:
            problems.append(f"{where}: split 이 {split!r} 이어야 합니다(현재 {row['split']!r})")
        if not isinstance(row["note"], str) or not row["note"].strip():
            problems.append(f"{where}: note 가 비어 있습니다")
        usable.append(row)
    return usable


def _check_messages(where: str, messages: Any, problems: list[str], *, minimum: int, maximum: int | None) -> bool:
    if not isinstance(messages, list) or len(messages) < minimum or (maximum is not None and len(messages) > maximum):
        limit = f"{minimum}~{maximum}" if maximum is not None else f"{minimum} 이상"
        problems.append(f"{where}: 메시지 수가 {limit} 이어야 합니다")
        return False
    ok = True
    for message in messages:
        if not isinstance(message, dict) or set(message) != {"role", "text"}:
            problems.append(f"{where}: 메시지는 role·text 두 필드여야 합니다")
            ok = False
        elif message["role"] not in ROLES or not str(message["text"]).strip():
            problems.append(f"{where}: 메시지의 role 은 {ROLES} 중 하나, text 는 비어 있지 않아야 합니다")
            ok = False
    if ok and not any(m["role"] == "고객" for m in messages):
        problems.append(f"{where}: 고객 메시지가 한 건도 없습니다")
        ok = False
    return ok


def _check_grounding(row: dict[str, Any], chunk_texts: set[str], problems: list[str]) -> None:
    where = f"grounding {row['id']}"
    label, kind = row["label"], row["kind"]
    allowed = {"grounded": GROUNDED_KINDS, "ungrounded": UNGROUNDED_KINDS}.get(label)
    if allowed is None:
        problems.append(f"{where}: label 은 grounded | ungrounded 여야 합니다(현재 {label!r})")
    elif kind not in allowed:
        problems.append(f"{where}: kind {kind!r} 는 {label} 문항의 종류가 아닙니다")
    answer = row["answer"]
    if not isinstance(answer, str) or not answer.strip():
        problems.append(f"{where}: answer 가 비어 있습니다")
        return
    span = row["wrong_span"]
    if label == "grounded" and span is not None:
        problems.append(f"{where}: grounded 문항에는 wrong_span 이 없어야 합니다")
    if label == "ungrounded" and (not isinstance(span, str) or not span.strip() or span not in answer):
        problems.append(f"{where}: ungrounded 문항의 wrong_span 은 답변 안의 구절이어야 합니다")
    _check_messages(where, row["conversation"], problems, minimum=1, maximum=None)
    name = row.get("customer_name")
    if name is not None and (not isinstance(name, str) or not name.strip()):
        problems.append(f"{where}: customer_name 은 비어 있지 않은 문자열이어야 합니다")

    evidence = row["evidence"]
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
        problems.append(f"{where}: 근거는 1~4건이어야 합니다")
        return
    ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict) or set(item) != set(EVIDENCE_FIELDS):
            problems.append(f"{where}: 근거 항목의 필드는 {EVIDENCE_FIELDS} 여야 합니다")
            continue
        eid, ekind, text = item["id"], item["kind"], item["text"]
        if eid in ids:
            problems.append(f"{where}: 근거 ID {eid} 가 중복됩니다")
        ids.add(eid)
        prefix = {"kb": "K", "tool": "T"}.get(ekind)
        if prefix is None or not re.match(rf"^{prefix}\d+$", str(eid)):
            problems.append(f"{where}: 근거 {eid} 의 kind·ID 가 맞지 않습니다(kb→K숫자, tool→T숫자)")
            continue
        if not str(item["title"]).strip():
            problems.append(f"{where}: 근거 {eid} 의 title 이 비어 있습니다")
        if ekind == "kb":
            if text not in chunk_texts:
                problems.append(f"{where}: 근거 {eid} 의 본문이 kb/ 의 어느 청크와도 글자 그대로 같지 않습니다")
        else:
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                parsed = None
            if not isinstance(parsed, dict):
                problems.append(f"{where}: 근거 {eid} 의 본문이 JSON 객체가 아닙니다")


def _check_resolution(row: dict[str, Any], problems: list[str]) -> None:
    where = f"resolution {row['id']}"
    if row["label"] not in RESOLUTION_LABELS:
        problems.append(f"{where}: label {row['label']!r} 은 허용된 값이 아닙니다")
    if not isinstance(row["kind"], str) or not row["kind"].strip():
        problems.append(f"{where}: kind 가 비어 있습니다")
    _check_messages(where, row["messages"], problems, minimum=2, maximum=8)


def _check_retrieval(row: dict[str, Any], doc_ids: set[str], chunk_ids: set[str], problems: list[str]) -> None:
    where = f"retrieval {row['id']}"
    if row["kind"] not in RETRIEVAL_KINDS:
        problems.append(f"{where}: kind {row['kind']!r} 은 허용된 값이 아닙니다")
    if not isinstance(row["query"], str) or not row["query"].strip():
        problems.append(f"{where}: query 가 비어 있습니다")
    docs, chunks = row["gold_docs"], row["gold_chunks"]
    if not isinstance(docs, list) or not docs or not isinstance(chunks, list) or not chunks:
        problems.append(f"{where}: gold_docs·gold_chunks 는 비어 있지 않은 목록이어야 합니다")
        return
    for doc in docs:
        if doc not in doc_ids:
            problems.append(f"{where}: 없는 문서 ID {doc!r}")
    for chunk in chunks:
        if chunk not in chunk_ids:
            problems.append(f"{where}: 없는 청크 ID {chunk!r}")
    if len(set(docs)) != len(docs) or len(set(chunks)) != len(chunks):
        problems.append(f"{where}: gold 목록에 중복이 있습니다")
    if {str(c).split("#")[0] for c in chunks} != set(docs):
        problems.append(f"{where}: gold_docs 는 gold_chunks 가 속한 문서와 같아야 합니다")
    if row["kind"] == "multi_doc" and len(chunks) < 2:
        problems.append(f"{where}: multi_doc 문항은 정답 청크가 둘 이상이어야 합니다")


def _check_overlap(
    name: str, by_split: dict[str, list[dict[str, Any]]], text_of: Callable[[dict[str, Any]], str], problems: list[str]
) -> None:
    for split, rows in by_split.items():
        seen: dict[str, str] = {}
        for row in rows:
            key = _normalize(text_of(row))
            if key in seen:
                problems.append(f"{name}/{split}: {seen[key]} 와 {row['id']} 의 본문이 같습니다")
            seen.setdefault(key, row["id"])
    dev = [(row["id"], _normalize(text_of(row)), _trigrams(text_of(row))) for row in by_split.get("dev", [])]
    for row in by_split.get("test", []):
        text = text_of(row)
        norm, grams = _normalize(text), _trigrams(text)
        for dev_id, dev_norm, dev_grams in dev:
            if norm == dev_norm:
                problems.append(f"{name}: dev {dev_id} 와 test {row['id']} 가 같은 문항입니다")
            elif _jaccard(grams, dev_grams) > NEAR_DUPLICATE_JACCARD:
                problems.append(f"{name}: dev {dev_id} 와 test {row['id']} 가 거의 같습니다(3-gram 자카드 > {NEAR_DUPLICATE_JACCARD})")


TEXT_OF: dict[str, Callable[[dict[str, Any]], str]] = {
    "grounding": lambda row: str(row["answer"]),
    "resolution": lambda row: " ".join(str(m.get("text", "")) for m in row["messages"] if isinstance(m, dict)),
    "retrieval": lambda row: str(row["query"]),
}


def load_all(root: Path = REPO_ROOT, problems: list[str] | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
    sink = problems if problems is not None else []
    return {name: {split: _load(root / "data" / "evals" / name / f"{split}.jsonl", sink) for split in SPLITS} for name in FIELDS}


def validate(root: Path = REPO_ROOT) -> list[str]:
    """문제 목록을 돌려준다. 빈 목록이면 통과."""
    problems: list[str] = []
    docs = load_docs(root / "kb")
    chunks = [chunk for doc in docs for chunk in split_chunks(doc)]
    doc_ids = {doc.doc_id for doc in docs}
    chunk_ids = {chunk.chunk_id for chunk in chunks}
    chunk_texts = {chunk.text for chunk in chunks}

    data = load_all(root, problems)
    for name, by_split in data.items():
        seen: set[str] = set()
        usable: dict[str, list[dict[str, Any]]] = {}
        for split, rows in by_split.items():
            usable[split] = _check_common(name, split, rows, seen, problems)
            for row in usable[split]:
                if name == "grounding":
                    _check_grounding(row, chunk_texts, problems)
                elif name == "resolution":
                    _check_resolution(row, problems)
                else:
                    _check_retrieval(row, doc_ids, chunk_ids, problems)
        _check_overlap(name, usable, TEXT_OF[name], problems)
    return problems


def summary(root: Path = REPO_ROOT) -> str:
    lines = []
    for name, by_split in load_all(root).items():
        for split, rows in by_split.items():
            by_kind = Counter(str(row.get("kind")) for row in rows)
            line = f"{name}/{split}.jsonl: {len(rows)}건"
            if name != "retrieval":
                by_label = Counter(str(row.get("label")) for row in rows)
                line += " | 라벨 " + ", ".join(f"{label} {count}" for label, count in sorted(by_label.items()))
            line += " | 종류 " + ", ".join(f"{kind} {count}" for kind, count in sorted(by_kind.items()))
            lines.append(line)
    return "\n".join(lines)


def main() -> int:
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else REPO_ROOT
    print(summary(root))
    problems = validate(root)
    for problem in problems:
        print(f"오류: {problem}", file=sys.stderr)
    if problems:
        print(f"평가 자료에 문제 {len(problems)}건", file=sys.stderr)
        return 1
    print("평가 자료 검사 통과: 문제 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
