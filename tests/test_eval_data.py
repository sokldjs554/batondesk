"""평가 자료 자체의 검증(형식, 중복, 근거 원문 일치, 평가용 동결)."""

from __future__ import annotations

import importlib.util
import json

from batondesk.evals.component import eval_retrieval
from batondesk.evals.data import check_frozen
from batondesk.kb.retriever import KnowledgeBase
from batondesk.settings import REPO_ROOT

DATA = REPO_ROOT / "data" / "evals"


def test_component_datasets_pass_the_validator() -> None:
    spec = importlib.util.spec_from_file_location("validate_eval_data", REPO_ROOT / "scripts" / "validate_eval_data.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.validate(REPO_ROOT) == []


def test_held_out_files_match_the_freeze_record() -> None:
    assert check_frozen(DATA) == [], "평가용(test) 자료를 바꿨다면 이유를 기록하고 동결 파일을 갱신해야 한다"


def test_retrieval_meets_the_ci_gate(kb: KnowledgeBase) -> None:
    gate = json.loads((REPO_ROOT / "eval_results" / "gate.json").read_text(encoding="utf-8"))["retrieval"]
    for split, minimums in gate.items():
        measured = eval_retrieval(kb, DATA, split)["modes"]["hybrid"]
        for metric, minimum in minimums.items():
            assert measured[metric] >= minimum, f"{split} {metric}: {measured[metric]} < {minimum}"


def test_synonym_expansion_only_appends(kb: KnowledgeBase) -> None:
    assert kb.expand("반품 배송비 얼마예요") == "반품 배송비 얼마예요"
    expanded = kb.expand("물건이 안 와요")
    assert expanded.startswith("물건이 안 와요 ") and "배송 지연" in expanded
