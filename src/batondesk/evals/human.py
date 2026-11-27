"""사람이 정답을 가린 채 매긴 라벨을, AI가 붙인 정답·판정기·채점 규칙과 견준다.

라벨은 /review 화면이 내보낸 JSON 이다. 정답표는 data/human_review/key.json 이다.
무엇을 답하는가
  - 근거 판정, 해결 판정: (1) 사람이 AI가 붙인 정답 라벨에 동의하는가 (2) 판정기(LLM)가 사람과 일치하는가
  - 에이전트 응대: 글자 비교 채점 규칙의 통과/실패가 사람이 읽은 올바름/잘못됨과 일치하는가.
    특히 "채점은 통과시켰는데 사람은 잘못됐다고 본 응대"를 찾는다(통과한 실행은 지금까지 아무도 읽지 않았다).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from batondesk.evals.stats import cohen_kappa

SUITE_TITLE = {"grounding": "근거 판정", "resolution": "해결 판정", "agent": "에이전트 응대"}
RESOLVED = ("resolved_confirmed", "resolved_assumed")


def _majority(verdicts: list[str]) -> str | None:
    return Counter(verdicts).most_common(1)[0][0] if verdicts else None


def agreement(labels_doc: dict[str, Any], key_doc: dict[str, Any]) -> dict[str, Any]:
    if labels_doc.get("kit") != key_doc["kit"]:
        raise ValueError(
            f"라벨의 표본({labels_doc.get('kit')})이 정답표의 표본({key_doc['kit']})과 다릅니다. 표본을 다시 만들었다면 그 표본으로 다시 검수해야 합니다."
        )
    labels: dict[str, dict[str, Any]] = labels_doc["labels"]
    unknown = sorted(set(labels) - set(key_doc["items"]))
    if unknown:
        raise ValueError(f"정답표에 없는 문항: {unknown}")
    suites: dict[str, Any] = {}
    for suite in SUITE_TITLE:
        keyed = {bid: k for bid, k in key_doc["items"].items() if k["suite"] == suite}
        done = {bid: labels[bid]["label"] for bid in keyed if bid in labels and labels[bid].get("label")}
        block: dict[str, Any] = {"sample": len(keyed), "labelled": len(done)}
        if done:
            block["human_vs_reference"] = cohen_kappa([(done[bid], keyed[bid]["gold"]) for bid in done])
            block["disagreements"] = [
                {
                    "id": bid,
                    "source_id": keyed[bid]["source_id"],
                    "kind": keyed[bid]["kind"],
                    "human": done[bid],
                    "reference": keyed[bid]["gold"],
                    "note": labels[bid].get("note") or "",
                }
                for bid in sorted(done)
                if done[bid] != keyed[bid]["gold"]
            ]
            block["human_vs_judge"] = {}
            judge_names = sorted({name for k in keyed.values() for name in k["judges"]})
            for name in judge_names:
                pairs = [(done[bid], _majority(keyed[bid]["judges"][name])) for bid in done]
                block["human_vs_judge"][name] = {
                    **cohen_kappa([(h, j) for h, j in pairs if j is not None]),
                    "judge_wrong_by_human": [bid for bid, (h, j) in zip(done, pairs, strict=True) if j is not None and h != j],
                }
            if suite == "resolution":
                binary = [
                    ("resolved" if done[bid] in RESOLVED else "not", "resolved" if keyed[bid]["gold"] in RESOLVED else "not")
                    for bid in done
                ]
                block["human_vs_reference_resolved_or_not"] = cohen_kappa(binary)
            if suite == "agent":
                # 채점은 통과시켰는데 사람은 잘못됐다고 본 응대, 그리고 그 반대
                block["grader_passed_but_human_says_wrong"] = [
                    keyed[bid]["source_id"] for bid in sorted(done) if keyed[bid]["gold"] == "correct" and done[bid] == "wrong"
                ]
                block["grader_failed_but_human_says_correct"] = [
                    keyed[bid]["source_id"] for bid in sorted(done) if keyed[bid]["gold"] == "wrong" and done[bid] == "correct"
                ]
        suites[suite] = block
    return {
        "kit": key_doc["kit"],
        "reviewer": labels_doc.get("reviewer"),
        "started_at": labels_doc.get("started_at"),
        "exported_at": labels_doc.get("exported_at"),
        "saw_answer_key": bool(labels_doc.get("saw_answer_key")),
        "reference_note": "근거·해결 판정의 기준은 AI가 붙인 정답 라벨이고, 에이전트 응대의 기준은 채점 규칙의 통과/실패다.",
        "suites": suites,
    }


def _line(name: str, block: dict[str, Any] | None) -> str:
    if not block or not block["n"]:
        return f"  {name}: 판정한 문항 없음"
    kappa = "계산 불가(한쪽이 한 가지 답만 함)" if block["kappa"] is None else f"{block['kappa']:.2f}"
    return f"  {name}: 일치 {block['agree']}/{block['n']} ({block['agreement']:.1%}), kappa {kappa}"


def text_report(result: dict[str, Any]) -> str:
    lines = [f"검수자 {result['reviewer']} · 표본 {result['kit']} · 정답표를 미리 봄: {'예' if result['saw_answer_key'] else '아니오'}"]
    for suite, title in SUITE_TITLE.items():
        block = result["suites"][suite]
        lines.append(f"\n[{title}] 표본 {block['sample']}건 중 {block['labelled']}건 판정")
        if not block["labelled"]:
            continue
        reference = "채점 규칙" if suite == "agent" else "AI가 붙인 정답"
        lines.append(_line(f"사람 대 {reference}", block["human_vs_reference"]))
        if suite == "resolution":
            lines.append(_line("사람 대 AI가 붙인 정답(해결/미해결 둘로만)", block["human_vs_reference_resolved_or_not"]))
        for name, judge in block["human_vs_judge"].items():
            lines.append(_line(f"사람 대 판정기 {name}", judge))
        for row in block["disagreements"]:
            lines.append(
                f"    어긋남 {row['id']} ({row['source_id']}, {row['kind']}): 사람 {row['human']} / 기준 {row['reference']}"
                + (f" — {row['note']}" if row["note"] else "")
            )
        if suite == "agent":
            lines.append(f"  채점은 통과, 사람은 잘못됨: {block['grader_passed_but_human_says_wrong'] or '없음'}")
            lines.append(f"  채점은 실패, 사람은 올바름: {block['grader_failed_but_human_says_correct'] or '없음'}")
    return "\n".join(lines)


def run(labels_path: Path, key_path: Path) -> dict[str, Any]:
    return agreement(json.loads(labels_path.read_text(encoding="utf-8")), json.loads(key_path.read_text(encoding="utf-8")))
