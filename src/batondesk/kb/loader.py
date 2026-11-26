"""지식 문서(markdown + front matter) 로더와 청크 분할.

문서는 `## ` 제목 단위로 나눈다. 정책 문서는 한 절이 한 가지 규칙을 다루도록 써 두었기
때문에 고정 길이 분할보다 절 단위가 근거 표시(어느 절을 인용했는가)에 맞다.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from batondesk.kb.models import Chunk, KnowledgeDoc

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_HEADING = re.compile(r"^## +(.+)$", re.M)


class KnowledgeFormatError(ValueError):
    pass


def _as_date(value: Any, field_name: str, path: Path) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise KnowledgeFormatError(f"{path}: {field_name} 은 날짜여야 합니다: {value!r}")


def parse_doc(path: Path) -> KnowledgeDoc:
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(raw)
    if not match:
        raise KnowledgeFormatError(f"{path}: front matter(--- ... ---)가 없습니다")
    meta = yaml.safe_load(match.group(1)) or {}
    for key in ("id", "title", "category", "doc_type", "effective_from"):
        if key not in meta:
            raise KnowledgeFormatError(f"{path}: front matter에 {key} 가 없습니다")
    if meta["doc_type"] not in ("policy", "notice", "faq", "guide"):
        raise KnowledgeFormatError(f"{path}: 알 수 없는 doc_type {meta['doc_type']!r}")
    effective_to = meta.get("effective_to")
    return KnowledgeDoc(
        doc_id=str(meta["id"]),
        title=str(meta["title"]),
        category=str(meta["category"]),
        doc_type=meta["doc_type"],
        effective_from=_as_date(meta["effective_from"], "effective_from", path),
        effective_to=_as_date(effective_to, "effective_to", path) if effective_to else None,
        body=match.group(2).strip(),
        path=str(path),
    )


def split_chunks(doc: KnowledgeDoc) -> list[Chunk]:
    """`## ` 제목마다 한 청크. 제목 앞의 머리말이 있으면 첫 청크로 둔다."""
    parts: list[tuple[str, str]] = []
    matches = list(_HEADING.finditer(doc.body))
    preamble = doc.body[: matches[0].start()].strip() if matches else doc.body.strip()
    if preamble:
        parts.append(("개요", preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(doc.body)
        text = doc.body[m.end() : end].strip()
        if text:
            parts.append((m.group(1).strip(), text))
    return [
        Chunk(
            chunk_id=f"{doc.doc_id}#{n}",
            doc_id=doc.doc_id,
            title=doc.title,
            heading=heading,
            text=text,
            category=doc.category,
            doc_type=doc.doc_type,
            effective_from=doc.effective_from,
            effective_to=doc.effective_to,
        )
        for n, (heading, text) in enumerate(parts, start=1)
    ]


def load_docs(kb_dir: Path) -> list[KnowledgeDoc]:
    docs = [parse_doc(p) for p in sorted(kb_dir.glob("*.md"))]
    ids = [d.doc_id for d in docs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise KnowledgeFormatError(f"중복된 문서 id: {sorted(dupes)}")
    return docs
