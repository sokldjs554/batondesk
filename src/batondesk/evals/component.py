"""부품별 평가: 검색, 근거 일치 판정기, 해결 여부 판정기.

에이전트 전체 평가와 따로 둔다. 전체 점수만 보면 검색이 틀렸는지, 답이 근거를 벗어났는지,
판정기가 틀렸는지 구분할 수 없기 때문이다.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from batondesk.agent.evidence import Evidence
from batondesk.checks.facts import check_facts
from batondesk.checks.grounding import GroundingChecker
from batondesk.checks.resolution import RESOLVED, ResolutionChecker
from batondesk.evals.data import read_jsonl, suite_path
from batondesk.evals.graders import normalize
from batondesk.evals.stats import cluster_ci, group
from batondesk.kb.retriever import KnowledgeBase, SearchMode
from batondesk.llm.base import LLMClient
from batondesk.obs.metrics import percentile

# ---- 검색 ---------------------------------------------------------------------


def eval_retrieval(kb: KnowledgeBase, root: Path, split: str, *, k_max: int = 10) -> dict[str, Any]:
    """구성별 검색 품질. 'hybrid' 가 서비스가 쓰는 구성(두 랭커 융합 + 유의어 확장)이다.

    적중 기준: 보통 질의는 정답 청크 중 하나가 k위 안에 있으면 적중, multi_doc 질의는 정답 청크가
    전부 k위 안에 있어야 적중이다.
    """
    items = read_jsonl(suite_path(root, "retrieval", split))
    out: dict[str, Any] = {"split": split, "items": len(items), "modes": {}, "misses": []}
    configs: tuple[tuple[str, SearchMode, bool], ...] = (
        ("hybrid", "hybrid", True),
        ("hybrid_no_synonyms", "hybrid", False),
        ("bm25", "bm25", False),
        ("ngram", "ngram", False),
    )
    for name, mode, expand in configs:
        ranks_chunk: list[int | None] = []
        ranks_doc: list[int | None] = []
        full_recall5: list[float] = []
        by_kind: dict[str, list[float]] = defaultdict(list)
        for item in items:
            hits = kb.search(item["query"], k=k_max, mode=mode, expand=expand)
            chunk_ids = [h.chunk.chunk_id for h in hits]
            doc_ids = [h.chunk.doc_id for h in hits]
            positions = [chunk_ids.index(g) + 1 if g in chunk_ids else None for g in item["gold_chunks"]]
            found = [p for p in positions if p is not None]
            if item["kind"] == "multi_doc":
                # 여러 문서를 함께 봐야 답할 수 있는 질의: 정답 청크가 "모두" 들어온 순위를 센다.
                chunk_rank = max(found) if len(found) == len(positions) else None
            else:
                chunk_rank = min(found) if found else None
            doc_rank = next((i for i, d in enumerate(doc_ids, start=1) if d in item["gold_docs"]), None)
            ranks_chunk.append(chunk_rank)
            ranks_doc.append(doc_rank)
            full_recall5.append(len(set(chunk_ids[:5]) & set(item["gold_chunks"])) / len(item["gold_chunks"]))
            hit5 = float(chunk_rank is not None and chunk_rank <= 5)
            by_kind[item["kind"]].append(hit5)
            if name == "hybrid" and not hit5:
                out["misses"].append(
                    {"id": item["id"], "kind": item["kind"], "query": item["query"], "gold": item["gold_chunks"], "top5": chunk_ids[:5]}
                )

        def hit_at(ranks: list[int | None], k: int) -> float:
            return round(sum(1 for r in ranks if r is not None and r <= k) / len(ranks), 4)

        out["modes"][name] = {
            "chunk_hit@1": hit_at(ranks_chunk, 1),
            "chunk_hit@3": hit_at(ranks_chunk, 3),
            "chunk_hit@5": hit_at(ranks_chunk, 5),
            "doc_hit@5": hit_at(ranks_doc, 5),
            "chunk_mrr@10": round(sum(1 / r for r in ranks_chunk if r) / len(ranks_chunk), 4),
            "gold_chunk_recall@5": round(sum(full_recall5) / len(full_recall5), 4),
            "chunk_hit@5_by_kind": {kind: round(sum(v) / len(v), 4) for kind, v in sorted(by_kind.items())},
        }
    out["kinds"] = dict(Counter(i["kind"] for i in items))
    return out


# ---- 판정기 공통 ---------------------------------------------------------------


def _binary(rows: list[dict[str, Any]], truth: str, predicted: str) -> dict[str, Any]:
    """양성 = 문제 있음(근거와 어긋남 / 미해결)."""
    tp = sum(1 for r in rows if r[truth] and r[predicted])
    fp = sum(1 for r in rows if not r[truth] and r[predicted])
    fn = sum(1 for r in rows if r[truth] and not r[predicted])
    tn = sum(1 for r in rows if not r[truth] and not r[predicted])
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "accuracy": round((tp + tn) / len(rows), 4) if rows else None,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "specificity": round(tn / (tn + fp), 4) if tn + fp else None,
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision and recall else None,
    }


def _usage(steps: list[dict[str, Any]]) -> dict[str, Any]:
    llm = [s for s in steps if s["type"] == "llm"]
    return {
        "calls": len(llm),
        "input_tokens": sum(s["usage"]["input_tokens"] for s in llm),
        "output_tokens": sum(s["usage"]["output_tokens"] for s in llm),
        "cost_usd": round(sum(s.get("cost_usd") or 0.0 for s in llm), 5),
        "latency_ms_p50": percentile([s["latency_ms"] for s in llm], 0.5),
        "latency_ms_p95": percentile([s["latency_ms"] for s in llm], 0.95),
        "models_served": dict(Counter(s["model"] for s in llm)),
    }


# ---- 근거 일치 판정기 -------------------------------------------------------------


async def eval_grounding(
    checker: GroundingChecker,
    root: Path,
    split: str,
    *,
    reps: int = 1,
    concurrency: int = 4,
    use_llm: bool = True,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    items = read_jsonl(suite_path(root, "grounding", split))
    semaphore = asyncio.Semaphore(concurrency)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    all_steps: list[dict[str, Any]] = []
    started = time.time()

    async def one(item: dict[str, Any], rep: int) -> None:
        evidence = [Evidence(id=e["id"], kind=e["kind"], title=e["title"], text=e["text"], turn_seq=0) for e in item["evidence"]]
        flags = check_facts(
            item["answer"],
            [(f"{e.title}\n{e.text}", e.kind == "tool") for e in evidence],
            extra_context="\n".join(m["text"] for m in item["conversation"] if m["role"] == "고객"),
        )
        row: dict[str, Any] = {
            "id": item["id"],
            "kind": item["kind"],
            "rep": rep,
            "truth_ungrounded": item["label"] == "ungrounded",
            "rules_hard": any(f.hard for f in flags),
            "rules_any": bool(flags),
            "fact_flags": [f.surface for f in flags],
        }
        if use_llm:
            async with semaphore:
                result, steps = await checker.check(item["answer"], evidence, item["conversation"], use_llm=True)
            all_steps.extend(steps)
            if result.mode != "llm+rules":
                errors.append(
                    {
                        "id": item["id"],
                        "rep": rep,
                        "failure": next((s.get("error") for s in steps if s["type"] == "check_error"), "judge_unavailable"),
                    }
                )
                return
            row["judge_ungrounded"] = not result.grounded
            row["claims"] = [c.model_dump() for c in result.claims]
            if item["label"] == "ungrounded" and item.get("wrong_span"):
                span = normalize(item["wrong_span"])
                flagged = [c for c in result.claims if c.label in ("contradicted", "unsupported")]
                row["located"] = any(span in normalize(c.claim) or normalize(c.claim) in span for c in flagged) if flagged else False
        rows.append(row)

    await asyncio.gather(*(one(item, rep) for item in items for rep in range(1, reps + 1)))
    rows.sort(key=lambda r: (r["id"], r["rep"]))
    summary: dict[str, Any] = {
        "split": split,
        "items": len(items),
        "reps": reps,
        "labels": dict(Counter(i["label"] for i in items)),
        "rows_scored": len(rows),
        "rows_errored": len(errors),
        "rules_hard_only": _binary(rows, "truth_ungrounded", "rules_hard"),
        "rules_any_flag": _binary(rows, "truth_ungrounded", "rules_any"),
        "wall_clock_s": round(time.time() - started, 1),
    }
    if use_llm:
        judged = [r for r in rows if "judge_ungrounded" in r]
        summary["judge"] = _binary(judged, "truth_ungrounded", "judge_ungrounded")
        correct = [{"id": r["id"], "v": float(r["truth_ungrounded"] == r["judge_ungrounded"])} for r in judged]
        summary["judge"]["accuracy_ci"] = cluster_ci(group(correct, "id", "v"))
        by_kind: dict[str, list[float]] = defaultdict(list)
        for r in judged:
            by_kind[r["kind"]].append(float(r["truth_ungrounded"] == r["judge_ungrounded"]))
        summary["judge"]["accuracy_by_kind"] = {k: round(sum(v) / len(v), 4) for k, v in sorted(by_kind.items())}
        located = [r["located"] for r in judged if "located" in r and r["judge_ungrounded"]]
        summary["judge"]["wrong_span_located"] = round(sum(located) / len(located), 4) if located else None
        per_item: dict[str, set[bool]] = defaultdict(set)
        for r in judged:
            per_item[r["id"]].add(r["judge_ungrounded"])
        summary["judge"]["items_with_same_verdict_every_rep"] = (
            round(sum(1 for v in per_item.values() if len(v) == 1) / len(per_item), 4) if per_item else None
        )
        summary["judge"]["wrong_items"] = sorted({r["id"] for r in judged if r["truth_ungrounded"] != r["judge_ungrounded"]})
        summary["judge"]["missed_ungrounded"] = sorted({r["id"] for r in judged if r["truth_ungrounded"] and not r["judge_ungrounded"]})
        summary["judge"]["false_alarms"] = sorted({r["id"] for r in judged if not r["truth_ungrounded"] and r["judge_ungrounded"]})
        summary["usage"] = _usage(all_steps)
        summary["checker"] = {
            "model": checker.model,
            "prompt": checker.prompt.id,
            "prompt_sha256": checker.prompt.sha256[:12],
            "effort": checker.effort,
        }
    _write(out_dir, rows, errors, summary)
    return {"summary": summary, "rows": rows, "errors": errors}


# ---- 해결 여부 판정기 -------------------------------------------------------------


async def eval_resolution(
    checker: ResolutionChecker, root: Path, split: str, *, reps: int = 1, concurrency: int = 4, out_dir: Path | None = None
) -> dict[str, Any]:
    items = read_jsonl(suite_path(root, "resolution", split))
    semaphore = asyncio.Semaphore(concurrency)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    all_steps: list[dict[str, Any]] = []
    started = time.time()

    async def one(item: dict[str, Any], rep: int) -> None:
        async with semaphore:
            result, steps = await checker.check(item["messages"])
        all_steps.extend(steps)
        if result.status == "unknown":
            errors.append(
                {
                    "id": item["id"],
                    "rep": rep,
                    "failure": next((s.get("error") for s in steps if s["type"] == "check_error"), "judge_unavailable"),
                }
            )
            return
        rows.append(
            {
                "id": item["id"],
                "kind": item.get("kind", ""),
                "rep": rep,
                "truth": item["label"],
                "predicted": result.status,
                "correct": item["label"] == result.status,
                "truth_not_resolved": item["label"] not in RESOLVED,
                "predicted_not_resolved": result.status not in RESOLVED,
                "reason": result.reason,
            }
        )

    await asyncio.gather(*(one(item, rep) for item in items for rep in range(1, reps + 1)))
    rows.sort(key=lambda r: (r["id"], r["rep"]))
    labels = ["resolved_confirmed", "resolved_assumed", "unresolved", "pending_customer", "not_applicable"]
    confusion = {t: {p: sum(1 for r in rows if r["truth"] == t and r["predicted"] == p) for p in labels} for t in labels}
    counted = [r for r in rows if r["truth"] in (*RESOLVED, "unresolved")]
    per_item: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        per_item[r["id"]].add(r["predicted"])
    correct = [{"id": r["id"], "v": float(r["correct"])} for r in rows]
    summary = {
        "split": split,
        "items": len(items),
        "reps": reps,
        "labels": dict(Counter(i["label"] for i in items)),
        "rows_scored": len(rows),
        "rows_errored": len(errors),
        "accuracy_5class": cluster_ci(group(correct, "id", "v")),
        "confusion": confusion,
        # 해결률에 들어가는 대화(해결·미해결)만 놓고, "미해결"을 양성으로 본 이진 지표
        "resolved_vs_unresolved": _binary(counted, "truth_not_resolved", "predicted_not_resolved"),
        "said_resolved_when_unresolved": sorted({r["id"] for r in rows if r["truth"] == "unresolved" and r["predicted"] in RESOLVED}),
        "wrong_items": sorted({r["id"] for r in rows if not r["correct"]}),
        "items_with_same_verdict_every_rep": round(sum(1 for v in per_item.values() if len(v) == 1) / len(per_item), 4)
        if per_item
        else None,
        "usage": _usage(all_steps),
        "checker": {
            "model": checker.model,
            "prompt": checker.prompt.id,
            "prompt_sha256": checker.prompt.sha256[:12],
            "effort": checker.effort,
        },
        "wall_clock_s": round(time.time() - started, 1),
    }
    _write(out_dir, rows, errors, summary)
    return {"summary": summary, "rows": rows, "errors": errors}


def _write(out_dir: Path | None, rows: list[dict[str, Any]], errors: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    if out_dir is None:
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in (("results.jsonl", rows), ("errors.jsonl", errors)):
        with (out_dir / name).open("w", encoding="utf-8") as handle:
            for row in data:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_checkers(llm: LLMClient | None, settings: Any, prompts: Any) -> tuple[GroundingChecker, ResolutionChecker]:
    grounding = GroundingChecker(
        llm,
        prompts.get("grounding", settings.grounding_prompt),
        settings.judge_model,
        effort=settings.judge_effort,
        max_tokens=settings.judge_max_tokens,
    )
    resolution = ResolutionChecker(
        llm, prompts.get("resolution", settings.resolution_prompt), settings.judge_model, effort=settings.judge_effort
    )
    return grounding, resolution
