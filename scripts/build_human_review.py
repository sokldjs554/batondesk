"""사람 검수용 표본을 만든다: 정답을 가린 문항 묶음(web/review-items.json)과 정답표(data/human_review/key.json).

왜 필요한가: 이 저장소의 평가 문항·정답·채점 규칙은 모두 AI가 썼다. 사람이 같은 문항을 정답을 보지 않고 직접
판정해 보면 (1) AI가 붙인 정답 라벨에 사람이 동의하는지, (2) 판정기(LLM)와 채점 규칙이 사람과 얼마나 일치하는지를
숫자로 말할 수 있다. 일치도 계산은 `python -m batondesk.evals human <라벨 파일>` 이 한다.

표본은 평가용(test) 자료에서 고정된 난수 씨앗으로 층화해 뽑는다(결과를 보고 고르지 않는다).
  - 근거 판정 30건: 답변이 근거와 맞는지(맞음 15, 어긋남 15, 종류 18가지가 모두 한 번 이상)
  - 해결 판정 18건: 대화가 해결됐는지 5분류(분류마다 3~4건)
  - 에이전트 응대 16건: 실제 모델이 한 평가용 응대가 올바른지(채점 규칙이 통과시킨 것 8, 떨어뜨린 것 8)

    python scripts/build_human_review.py            # 두 파일을 다시 만든다
    python scripts/build_human_review.py --check    # 저장소의 파일이 지금 자료로 만든 것과 같은지 확인(CI)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from batondesk.evals.data import load_agent_cases, read_jsonl
from batondesk.evals.graders import regrade_text_checks
from batondesk.kb.retriever import KnowledgeBase

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "evals"
RUNS = ROOT / "eval_results" / "runs"
ITEMS_PATH = ROOT / "web" / "review-items.json"
KEY_PATH = ROOT / "data" / "human_review" / "key.json"

SEED = 20261127
# 판정기·채점 규칙의 결과를 읽어 올 실행(평가용, 2026-11-26)
RUN_GROUNDING = {"grounding-v4": "20261126-124412-grounding-test-test-g4", "grounding-v1": "20261126-124414-grounding-test-test-g1"}
RUN_RESOLUTION = "20261126-124412-resolution-test-test-r1"
RUN_AGENT = "20261126-124400-agent-test-test-a4-g4"
SESSION = "2026-11-20 (금) 10:00 KST"

RESOLUTION_PER_LABEL = {"resolved_confirmed": 4, "resolved_assumed": 4, "unresolved": 4, "pending_customer": 3, "not_applicable": 3}
ACTION_LABEL = {"answer": "답변", "ask": "되묻기", "handoff": "상담원에게 넘김"}
ROLE_LABEL = {"customer": "고객", "ai": "AI", "agent": "상담원", "system": "시스템"}


def _rows(run: str) -> list[dict[str, Any]]:
    return read_jsonl(RUNS / run / "results.jsonl")


def _grounding(rng: random.Random) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = read_jsonl(DATA / "grounding" / "test.jsonl")
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_kind[item["kind"]].append(item)
    picked: list[dict[str, Any]] = [rng.choice(by_kind[kind]) for kind in sorted(by_kind)]  # 종류마다 한 건
    for label, quota in (("grounded", 15), ("ungrounded", 15)):
        have = [p for p in picked if p["label"] == label]
        rest = [i for i in items if i["label"] == label and i not in picked]
        rng.shuffle(rest)
        picked.extend(rest[: quota - len(have)])
    verdicts: dict[str, dict[str, list[bool]]] = {name: defaultdict(list) for name in RUN_GROUNDING}
    for name, run in RUN_GROUNDING.items():
        for row in _rows(run):
            verdicts[name][row["id"]].append(bool(row["judge_ungrounded"]))
    blind, key = [], {}
    for item in picked:
        blind.append(
            {
                "suite": "grounding",
                "session": SESSION + (f" · 고객 이름: {item['customer_name']}" if item.get("customer_name") else ""),
                "evidence": [{"id": e["id"], "kind": e["kind"], "title": e["title"], "text": e["text"]} for e in item["evidence"]],
                "conversation": item["conversation"],
                "answer": item["answer"],
                "_source": item["id"],
            }
        )
        key[item["id"]] = {
            "suite": "grounding",
            "source_id": item["id"],
            "kind": item["kind"],
            "gold": item["label"],
            "judges": {name: ["ungrounded" if v else "grounded" for v in verdicts[name][item["id"]]] for name in RUN_GROUNDING},
        }
    return blind, key


def _resolution(rng: random.Random) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = read_jsonl(DATA / "resolution" / "test.jsonl")
    predicted: dict[str, list[str]] = defaultdict(list)
    for row in _rows(RUN_RESOLUTION):
        predicted[row["id"]].append(row["predicted"])
    blind, key = [], {}
    for label, quota in RESOLUTION_PER_LABEL.items():
        pool = [i for i in items if i["label"] == label]
        rng.shuffle(pool)
        for item in pool[:quota]:
            blind.append({"suite": "resolution", "messages": item["messages"], "_source": item["id"]})
            key[item["id"]] = {
                "suite": "resolution",
                "source_id": item["id"],
                "kind": item["kind"],
                "gold": item["label"],
                "judges": {"resolution-v1": predicted[item["id"]]},
            }
    return blind, key


def _agent(rng: random.Random, kb: KnowledgeBase) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases = {c.id: c for c in load_agent_cases(DATA, "test")}
    rows = [regrade_text_checks(cases[r["case_id"]], r) for r in _rows(RUN_AGENT) if not r.get("skipped")]
    failed_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    passed_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        (passed_by_case if row["passed"] else failed_by_case)[row["case_id"]].append(row)
    failed_cases = sorted(failed_by_case)
    rng.shuffle(failed_cases)
    picked = [rng.choice(failed_by_case[cid]) for cid in failed_cases[:8]]
    # 통과한 응대는 분류가 겹치지 않게, 떨어진 문항과 다른 문항에서 뽑는다.
    by_category: dict[str, list[str]] = defaultdict(list)
    for cid in sorted(passed_by_case):
        if cid not in failed_by_case:
            by_category[cases[cid].category].append(cid)
    categories = sorted(by_category)
    rng.shuffle(categories)
    for category in categories[: 16 - len(picked)]:
        picked.append(rng.choice(passed_by_case[rng.choice(by_category[category])]))
    blind, key = [], {}
    for row in picked:
        case = cases[row["case_id"]]
        reference = [
            {"title": f"{chunk.title} › {chunk.heading}", "text": chunk.text}
            for doc in case.expect.gold_docs
            for chunk in kb.chunks_of(doc)
        ]
        source = f"{row['case_id']}#{row['rep']}"
        blind.append(
            {
                "suite": "agent",
                "session": SESSION,
                "category": case.category,
                "transcript": [{"role": ROLE_LABEL.get(m["role"], m["role"]), "text": m["text"]} for m in row["transcript"]],
                "ended_in_handoff": bool(row["actual_handoff"]),
                "rubric": {
                    "expected_action": " 또는 ".join(ACTION_LABEL[a] for a in case.expect.action),
                    "why": case.note.split(" (2026-")[0],
                    "order_system_fault": case.fault if case.fault != "none" else None,
                    "injected_wrong_draft": case.inject_draft,
                },
                "reference": reference,
                "_source": source,
            }
        )
        key[source] = {
            "suite": "agent",
            "source_id": source,
            "kind": case.category,
            "gold": "correct" if row["passed"] else "wrong",  # 여기서 "정답"은 채점 규칙의 판정이다
            "judges": {},
        }
    return blind, key


def build() -> tuple[dict[str, Any], dict[str, Any]]:
    rng = random.Random(SEED)
    kb = KnowledgeBase.from_dir(ROOT / "kb")
    blind: list[dict[str, Any]] = []
    key: dict[str, Any] = {}
    for part in (_grounding(rng), _resolution(rng), _agent(rng, kb)):
        blind.extend(part[0])
        key.update(part[1])
    # 묶음 안에서 순서를 섞고, 원래 ID 대신 가린 ID를 붙인다.
    prefix = {"grounding": "G", "resolution": "R", "agent": "A"}
    items: list[dict[str, Any]] = []
    blind_key: dict[str, Any] = {}
    for suite in ("grounding", "resolution", "agent"):
        part = [b for b in blind if b["suite"] == suite]
        rng.shuffle(part)
        for index, item in enumerate(part, start=1):
            blind_id = f"{prefix[suite]}{index:02d}"
            source = item.pop("_source")
            items.append({"id": blind_id, **item})
            blind_key[blind_id] = key[source]
    kit_id = hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]
    items_doc = {
        "kit": kit_id,
        "note": "사람 검수용 표본. 정답과 판정기 결과는 들어 있지 않다(data/human_review/key.json 에 따로 있다).",
        "counts": {suite: sum(1 for i in items if i["suite"] == suite) for suite in ("grounding", "resolution", "agent")},
        "items": items,
    }
    key_doc = {
        "kit": kit_id,
        "seed": SEED,
        "note": "검수를 마치기 전에는 열어 보지 않는다. 열어 봤다면 그 사실을 결과에 적는다.",
        "sources": {
            "grounding_judges": RUN_GROUNDING,
            "resolution_judge": RUN_RESOLUTION,
            "agent_run": RUN_AGENT,
            "agent_grading": "지금의 채점 규칙",
        },
        "items": blind_key,
    }
    return items_doc, key_doc


def _dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    items_doc, key_doc = build()
    if args.check:
        stale = [
            str(p.relative_to(ROOT))
            for p, doc in ((ITEMS_PATH, items_doc), (KEY_PATH, key_doc))
            if not p.exists() or p.read_text(encoding="utf-8") != _dump(doc)
        ]
        if stale:
            print(f"사람 검수 표본이 지금 자료와 다릅니다: {stale}. scripts/build_human_review.py 를 다시 실행하세요.", file=sys.stderr)
            return 1
        print(f"사람 검수 표본이 지금 자료와 같습니다(kit {items_doc['kit']}, {items_doc['counts']}).")
        return 0
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ITEMS_PATH.write_text(_dump(items_doc), encoding="utf-8")
    KEY_PATH.write_text(_dump(key_doc), encoding="utf-8")
    print(f"kit {items_doc['kit']}: {items_doc['counts']} → {ITEMS_PATH.relative_to(ROOT)}, {KEY_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
