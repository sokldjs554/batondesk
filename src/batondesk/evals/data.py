"""평가 자료 읽기와 동결(freeze) 확인.

평가용(test) 자료는 프롬프트를 고치는 데 쓰지 않는다. 실수로라도 바뀌지 않게
`data/evals/FROZEN.json` 에 파일별 sha256 을 적어 두고 CI에서 대조한다.
평가용 자료를 고쳐야 할 때는(채점 기준 오류 등) 이유를 docs/evaluation.md 의 변경 기록에 남기고
동결 파일을 갱신한다 — 조용히 바꾸는 일이 없게 하려는 장치다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from batondesk.clock import KST

# 모든 평가는 이 시각으로 예시 주문 시스템의 시계를 고정한다.
EVAL_NOW = datetime(2026, 11, 20, 10, 0, tzinfo=KST)
SUITES = ("agent", "grounding", "resolution", "retrieval")
Split = Literal["dev", "test"]


class Expect(BaseModel):
    action: list[Literal["answer", "ask", "handoff"]]
    handoff_reason: list[str] = Field(default_factory=list)
    must_include: list[list[str]] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    gold_docs: list[str] = Field(default_factory=list)
    tools_any: list[list[str]] = Field(default_factory=list)
    tools_none: list[str] = Field(default_factory=list)
    end_state: dict[str, str] = Field(default_factory=dict)
    abstain: bool = False
    kb_issue: bool = False


class AgentCase(BaseModel):
    id: str
    split: Split
    category: str
    customer_id: str
    fault: Literal["none", "timeout", "error", "flaky", "slow"] = "none"
    turns: list[str]
    expect: Expect
    inject_draft: str | None = None
    note: str = ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError as exc:
            raise ValueError(f"{path}:{number}: JSON 오류 — {exc}") from exc
    return rows


def suite_path(root: Path, suite: str, split: str) -> Path:
    return root / suite / f"{split}.jsonl"


def load_agent_cases(root: Path, split: str) -> list[AgentCase]:
    return [AgentCase.model_validate(r) for r in read_jsonl(suite_path(root, "agent", split))]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frozen_path(root: Path) -> Path:
    return root / "FROZEN.json"


def freeze(root: Path) -> dict[str, Any]:
    files = {}
    for suite in SUITES:
        path = suite_path(root, suite, "test")
        if path.exists():
            files[f"{suite}/test.jsonl"] = {"sha256": file_sha256(path), "items": len(read_jsonl(path))}
    data = {"note": "평가용(test) 자료의 동결 기록. 바꾸려면 docs/evaluation.md 의 변경 기록에 이유를 남긴다.", "files": files}
    frozen_path(root).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def check_frozen(root: Path) -> list[str]:
    path = frozen_path(root)
    if not path.exists():
        return ["data/evals/FROZEN.json 이 없습니다"]
    recorded = json.loads(path.read_text(encoding="utf-8"))["files"]
    problems = []
    for name, info in recorded.items():
        target = root / name
        if not target.exists():
            problems.append(f"{name}: 동결된 파일이 없어졌습니다")
        elif file_sha256(target) != info["sha256"]:
            problems.append(f"{name}: 동결된 평가용 자료가 바뀌었습니다")
    for suite in SUITES:
        if suite_path(root, suite, "test").exists() and f"{suite}/test.jsonl" not in recorded:
            problems.append(f"{suite}/test.jsonl: 동결 기록에 없습니다")
    return problems
