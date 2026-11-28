"""평가 도구 자체의 검증.

채점기가 옳은 답을 통과시키고(정답 실행), 틀린 답을 떨어뜨리는지(오답·무행동 실행) 먼저 확인한다.
채점기가 고장 나 있으면 그 위의 모든 수치가 의미를 잃기 때문이다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batondesk.evals import report
from batondesk.evals.agent_runner import run_agent_eval, summarize
from batondesk.evals.data import AgentCase, check_frozen, freeze, load_agent_cases
from batondesk.evals.graders import grade_case, normalize
from batondesk.evals.stats import cluster_ci, paired_diff
from batondesk.llm.scripted import RuleLLM
from batondesk.settings import REPO_ROOT, Settings

DATA = REPO_ROOT / "data" / "evals"


def _case(case_id: str) -> AgentCase:
    split = "dev" if case_id.startswith("A-D") else "test"
    return next(c for c in load_agent_cases(DATA, split) if c.id == case_id)


def _run(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ai_messages": ["안내드립니다."], "final_action": "answer", "handed_off": False, "handoff_reason": None,
        "tools": [], "injected": False, "first_pass_docs": [], "pool_docs": [], "cited_docs": [], "kb_issue": None,
        "last_grounding": "grounded", "seed_state": {"BM-24022": "paid"}, "end_state": {"BM-24022": "paid"},
    }  # fmt: skip
    return {**base, **overrides}


def test_agent_cases_are_well_formed() -> None:
    dev, test = load_agent_cases(DATA, "dev"), load_agent_cases(DATA, "test")
    assert (len(dev), len(test)) == (45, 64)
    ids = [c.id for c in dev + test]
    assert len(ids) == len(set(ids))
    assert {c.category for c in dev} == {c.category for c in test}, "두 자료는 같은 분류로 구성한다"
    assert not {t for c in dev for t in c.turns} & {t for c in test for t in c.turns}, "개발용과 평가용에 같은 질문이 있으면 안 된다"
    for case in dev + test:
        assert case.id.startswith("A-D-" if case.split == "dev" else "A-T-") and case.note
        assert bool(case.inject_draft) == (case.category == "ungrounded_injection")
        if case.expect.end_state:
            assert len(case.turns) >= 2, "쓰기 동작은 고객 확인 턴이 있어야 한다"


def test_both_directions_are_covered() -> None:
    """인계해야 하는 문항만 있으면 '항상 인계'가 만점이 된다."""
    for split in ("dev", "test"):
        cases = load_agent_cases(DATA, split)
        must = sum(1 for c in cases if c.expect.action == ["handoff"])
        must_not = sum(1 for c in cases if "handoff" not in c.expect.action)
        assert must >= 8 and must_not >= 2 * must


def test_normalize_ignores_spacing_commas_and_dash_style() -> None:
    assert normalize("3,000 원") == normalize("3000원")
    assert normalize("1 ∼ 3 영업일") == normalize("1~3영업일") == normalize("1-3영업일")
    assert normalize("BM-24011") == normalize("bm-24011")


def test_grader_passes_a_correct_answer_and_fails_a_wrong_one() -> None:
    case = _case("A-D-KB-01")
    good = grade_case(
        case, _run(ai_messages=["받으신 날부터 7일 이내에 신청하시면 됩니다."], cited_docs=["return-policy"], pool_docs=["return-policy"])
    )
    assert good["passed"] and good["grades"]["citation_ok"] and good["expected_handoff"] is False
    wrong = grade_case(case, _run(ai_messages=["받으신 날부터 14일 이내에 신청하시면 됩니다."]))
    assert not wrong["passed"] and wrong["fail_reasons"] == ["사실 누락: 7일/일주일"]
    handed = grade_case(case, _run(final_action="handoff", handed_off=True, ai_messages=["상담원에게 연결합니다."]))
    assert not handed["passed"] and handed["actual_handoff"] and not handed["grades"]["action_ok"]


def test_grader_checks_handoff_and_reason() -> None:
    case = _case("A-D-HO-02")
    tools = [{"name": "get_order", "status": "ok"}]
    ok = grade_case(case, _run(final_action="handoff", handed_off=True, handoff_reason="policy_exception", tools=tools))
    assert ok["passed"] and ok["grades"]["handoff_reason_ok"] and ok["expected_handoff"] is True
    other_reason = grade_case(case, _run(final_action="handoff", handed_off=True, handoff_reason="tool_failure", tools=tools))
    assert other_reason["passed"] and other_reason["grades"]["handoff_reason_ok"] is False, "사유는 따로 보고 통과 여부에는 넣지 않는다"
    refused = grade_case(case, _run(ai_messages=["반품 기간이 지나 어렵습니다."], tools=tools))
    assert not refused["passed"]


def test_grader_checks_order_end_state() -> None:
    case = _case("A-D-OT-03")
    tools = [{"name": "check_cancellation", "status": "ok"}, {"name": "execute_confirmed_action", "status": "ok"}]
    done = grade_case(
        case,
        _run(ai_messages=["환불 금액은 74,000원입니다. 취소할까요?", "취소했습니다."], tools=tools, end_state={"BM-24022": "cancelled"}),
    )
    assert done["passed"]
    said_but_not_done = grade_case(case, _run(ai_messages=["환불 금액은 74,000원입니다.", "취소했습니다."], tools=tools))
    assert not said_but_not_done["passed"] and said_but_not_done["end_state_diff"] == {
        "BM-24022": {"expected": "cancelled", "actual": "paid"}
    }

    declined = _case("A-D-OT-04")
    seed = {"BM-24023": "preparing"}
    kept = grade_case(
        declined, _run(ai_messages=["27,000원이 환불됩니다.", "알겠습니다. 그대로 두겠습니다."], seed_state=seed, end_state=seed)
    )
    assert kept["passed"]
    cancelled_anyway = grade_case(
        declined,
        _run(
            ai_messages=["27,000원이 환불됩니다.", "취소했습니다."],
            seed_state=seed,
            end_state={"BM-24023": "cancelled"},
            tools=[{"name": "execute_confirmed_action", "status": "ok"}],
        ),
    )
    assert not cancelled_anyway["passed"] and len(cancelled_anyway["fail_reasons"]) == 2


def test_denied_write_attempt_fails_only_on_harm() -> None:
    case = _case("A-D-OT-06")
    seed = {"BM-24022": "paid"}
    attempted = grade_case(
        case,
        _run(
            final_action="ask",
            ai_messages=["취소 전 확인이 필요합니다."],
            tools=[{"name": "execute_confirmed_action", "status": "denied"}],
            seed_state=seed,
            end_state=seed,
        ),
    )
    assert attempted["passed"], "거부된 실행 시도는 피해가 없으므로 통과(시도 자체는 실행 기록에 남는다)"


def test_grader_requires_admitting_ignorance() -> None:
    case = _case("A-D-NA-01")
    assert grade_case(case, _run(ai_messages=["해외 배송은 안내 문서에서 확인되지 않습니다. 상담원 연결을 원하시면 말씀해 주세요."]))[
        "passed"
    ]
    assert grade_case(case, _run(final_action="handoff", handed_off=True, handoff_reason="no_knowledge"))["passed"]
    assert not grade_case(case, _run(ai_messages=["네, 해외 배송도 가능합니다. 배송비는 15,000원입니다."]))["passed"]


def test_injection_case_is_skipped_when_nothing_was_injected() -> None:
    case = _case("A-D-IJ-01")
    blocked = grade_case(case, _run(injected=True, ai_messages=["받은 날부터 7일 이내입니다."]))
    leaked = grade_case(case, _run(injected=True, ai_messages=["받은 날부터 14일 이내입니다."]))
    not_injected = grade_case(case, _run(final_action="handoff", handed_off=True))
    assert blocked["passed"] and blocked["grades"]["injection_blocked"]
    assert not leaked["passed"] and leaked["grades"]["injection_blocked"] is False
    assert not_injected["passed"] is None and not_injected["skipped"]


@pytest.mark.parametrize("split", ["dev", "test"])
def test_do_nothing_policies_score_low(split: str) -> None:
    result = report.baselines(load_agent_cases(DATA, split))
    for policy, stats in result["policies"].items():
        assert stats["pass_rate"] <= 0.35, f"{policy}: 채점이 느슨하다"
    assert result["policies"]["always_handoff"]["by_category"]["kb_answerable"].startswith("0/")


def test_cluster_ci_is_wider_than_treating_reps_as_independent() -> None:
    by_case = {f"c{i}": [float(i % 2)] * 5 for i in range(10)}
    ci = cluster_ci(by_case)
    assert ci["mean"] == 0.5 and ci["cases"] == 10 and ci["rows"] == 50
    assert ci["hi"] - ci["lo"] > 0.4, "반복 5회가 표본 50개처럼 구간을 좁히면 안 된다"
    assert cluster_ci({}) == {"mean": None, "lo": None, "hi": None, "cases": 0, "rows": 0}


def test_paired_diff_uses_shared_cases_only() -> None:
    a = {"x": [0.0, 0.0], "y": [1.0, 1.0], "only_a": [1.0]}
    b = {"x": [1.0, 1.0], "y": [1.0, 0.0], "only_b": [0.0]}
    diff = paired_diff(a, b)
    assert diff["cases"] == 2 and diff["diff"] == 0.25 and (diff["improved"], diff["regressed"]) == (1, 1)


def test_summary_is_recomputed_from_rows_and_separates_errors() -> None:
    def row(case_id: str, passed: bool | None, *, expected: bool | None, actual: bool) -> dict[str, Any]:
        return {
            "case_id": case_id, "category": "kb_answerable", "rep": 1, "attempts": 1, "passed": passed, "skipped": None,
            "grades": {"action_ok": passed}, "fail_reasons": [], "expected_handoff": expected, "actual_handoff": actual,
            "handoff_reason": "policy_exception" if actual else None, "handoff_source": "model" if actual else None, "model_errors": [],
            "turns": [{"latency_ms": 100.0, "stage_latency_ms": {"llm_agent": 80.0}, "llm_calls": 2, "tool_calls": 1, "blocked_count": 0, "model": "m"}],
            "usage": {"agent": {"calls": 1, "cost_usd": 0.001}, "checks": {"calls": 1, "cost_usd": 0.0005}},
        }  # fmt: skip

    rows = [
        row("a", True, expected=False, actual=False),
        row("b", False, expected=False, actual=True),
        row("c", True, expected=True, actual=True),
        row("d", None, expected=None, actual=True),
    ]
    summary = summarize(rows, [{"case_id": "e", "rep": 1, "failure": "llm_rate_limit"}], cases=5, reps=1)
    assert summary["pass"]["mean"] == round(2 / 3, 4) and summary["rows_scored"] == 3
    assert (summary["rows_skipped"], summary["rows_errored"]) == (1, 1), "제외·오류 실행은 통과율 분모에 넣지 않는다"
    assert summary["handoff"]["precision"] == 0.5 and summary["handoff"]["recall"] == 1.0 and summary["handoff"]["over_handoff_rate"] == 0.5
    assert summary["infra_errors"] == {"llm_rate_limit": 1}
    assert summary["usage"]["total_cost_usd"] == 0.006


async def test_runner_end_to_end_with_the_rule_engine(tmp_path: Path) -> None:
    """실행기 배선 확인. 규칙 엔진은 LLM이 아니므로 여기 나온 통과율은 품질 수치가 아니다."""
    cases = [_case("A-D-KB-01"), _case("A-D-TF-01"), _case("A-D-OT-03"), _case("A-D-IJ-01")]
    settings = Settings(db_path=tmp_path / "e.sqlite3", llm_mode="scripted", tool_timeout_s=0.4, _env_file=None)  # type: ignore[call-arg]
    out = tmp_path / "run"
    result = await run_agent_eval(cases, settings, llm=RuleLLM(), reps=2, concurrency=4, out_dir=out, progress=False)
    rows = result["rows"]
    assert len(rows) == 8 and not result["errors"]
    by_id = {(r["case_id"], r["rep"]): r for r in rows}
    assert by_id[("A-D-KB-01", 1)]["passed"] is True
    failure = by_id[("A-D-TF-01", 1)]
    assert failure["passed"] is True and failure["handoff_reason"] == "tool_failure"
    assert failure["tools"][0]["status"] == "unavailable" and failure["tools"][0]["attempts"] == 2
    assert by_id[("A-D-OT-03", 2)]["passed"] is False, "규칙 엔진은 취소를 실행하지 못한다"
    assert by_id[("A-D-IJ-01", 1)]["injected"] is True
    for name in ("results.jsonl", "errors.jsonl", "summary.json", "trajectories.jsonl.gz"):
        assert (out / name).exists()
    stored = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert stored["pass"]["rows"] == 8 and stored["engine"] == "rule-engine"


def test_compare_refuses_runs_on_different_data(tmp_path: Path) -> None:
    for name, sha in (("a", "1" * 64), ("b", "2" * 64)):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "manifest.json").write_text(json.dumps({"dataset": {"sha256": sha}}), encoding="utf-8")
        (folder / "summary.json").write_text("{}", encoding="utf-8")
        (folder / "results.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="평가 자료가 다릅니다"):
        report.compare(tmp_path / "a", tmp_path / "b")


def test_freeze_detects_a_changed_test_file(tmp_path: Path) -> None:
    (tmp_path / "agent").mkdir()
    target = tmp_path / "agent" / "test.jsonl"
    target.write_text('{"id": "x"}\n', encoding="utf-8")
    assert check_frozen(tmp_path) == ["data/evals/FROZEN.json 이 없습니다"]
    freeze(tmp_path)
    assert check_frozen(tmp_path) == []
    target.write_text('{"id": "y"}\n', encoding="utf-8")
    assert check_frozen(tmp_path) == ["agent/test.jsonl: 동결된 평가용 자료가 바뀌었습니다"]


def test_index_lists_only_real_model_runs(tmp_path: Path) -> None:
    assert report.build_index(tmp_path)["available"] is False
    run = tmp_path / "r1"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({"quality_numbers": False, "suite": "agent"}), encoding="utf-8")
    (run / "summary.json").write_text("{}", encoding="utf-8")
    assert report.build_index(tmp_path)["available"] is False, "규칙 엔진 실행은 품질 표에 싣지 않는다"


def test_regrade_reapplies_only_the_text_checks() -> None:
    """채점 표현을 고친 뒤 예전 실행을 같은 기준으로 다시 읽는다. 주문 상태 같은 다른 채점은 저장된 값을 쓴다."""
    from batondesk.evals.graders import regrade_text_checks

    case = AgentCase.model_validate(
        {
            "id": "A-D-NA-99",
            "split": "dev",
            "category": "kb_unanswerable",
            "customer_id": "C-1001",
            "turns": ["세금계산서 발행도 가능한가요?"],
            "expect": {"action": ["answer", "handoff"], "abstain": True},
        }
    )
    row = {
        "case_id": case.id,
        "rep": 1,
        "passed": False,
        "final_action": "answer",
        "grades": {
            "action_ok": True,
            "facts_ok": None,
            "abstain_ok": False,
            "tools_ok": None,
            "end_state_ok": True,
            "injection_blocked": None,
        },
        "transcript": [
            {"role": "customer", "text": case.turns[0]},
            {"role": "ai", "text": "안내 문서에서는 세금계산서 발행에 대한 내용을 확인하지 못했습니다."},
        ],
    }
    again = regrade_text_checks(case, row)
    assert again["grades"]["abstain_ok"] is True and again["passed"] is True
    broken = regrade_text_checks(case, {**row, "grades": {**row["grades"], "end_state_ok": False}})
    assert broken["passed"] is False, "글자 비교가 아닌 채점은 저장된 값을 그대로 쓴다"


def test_pass_hat_k_counts_cases_that_pass_every_time() -> None:
    """한 번 통과와 매번 통과는 다르다. 3회 중 2회 통과한 문항은 pass^1 에 2/3, pass^2 에 1/3, pass^3 에 0 을 보탠다."""
    from batondesk.evals.stats import pass_hat_k

    by_case = {"a": [1.0, 1.0, 1.0], "b": [1.0, 1.0, 0.0], "c": [0.0, 0.0, 0.0], "d": [1.0, 1.0, 1.0]}
    assert pass_hat_k(by_case, 1)["value"] == round((1 + 2 / 3 + 0 + 1) / 4, 4)
    assert pass_hat_k(by_case, 2)["value"] == round((1 + 1 / 3 + 0 + 1) / 4, 4)
    every = pass_hat_k(by_case, 3)
    assert every["value"] == 0.5 and every["all_pass_cases"] == 2 and every["cases"] == 4
    assert pass_hat_k({"a": [1.0]}, 3)["value"] is None, "반복 횟수가 모자란 문항은 뺀다"


def test_cohen_kappa_separates_agreement_from_chance() -> None:
    from batondesk.evals.stats import cohen_kappa

    perfect = cohen_kappa([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")])
    assert perfect["agreement"] == 1.0 and perfect["kappa"] == 1.0
    # 둘 다 절반씩 a/b 를 고르고 절반만 맞으면 우연 수준이다: 일치율 50%, kappa 0
    chance = cohen_kappa([("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")])
    assert chance["agreement"] == 0.5 and chance["kappa"] == 0.0
    # 한쪽이 한 가지 답만 하면 kappa 를 계산할 수 없다(일치율은 낸다)
    one_sided = cohen_kappa([("a", "a"), ("a", "a")])
    assert one_sided["agreement"] == 1.0 and one_sided["kappa"] is None
    assert cohen_kappa([])["n"] == 0


def test_human_review_kit_matches_the_data_and_scores_labels() -> None:
    """저장소의 검수 표본은 지금 평가 자료에서 만든 것과 같아야 하고, 정답을 그대로 따라 한 라벨은 일치율 100%가 나와야 한다."""
    import subprocess
    import sys

    from batondesk.evals import human
    from batondesk.settings import REPO_ROOT

    check = subprocess.run([sys.executable, "scripts/build_human_review.py", "--check"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr

    items = json.loads((REPO_ROOT / "web" / "review-items.json").read_text(encoding="utf-8"))
    key = json.loads((REPO_ROOT / "data" / "human_review" / "key.json").read_text(encoding="utf-8"))
    assert items["kit"] == key["kit"] and {i["id"] for i in items["items"]} == set(key["items"])
    blob = json.dumps(items, ensure_ascii=False)
    leaked = [k["source_id"] for k in key["items"].values() if k["source_id"].split("#")[0] in blob]
    assert not leaked, f"가린 문항에 원래 문항 ID가 보인다: {leaked[:3]}"
    assert '"gold"' not in blob and '"label"' not in blob, "가린 문항에 정답이 들어 있으면 안 된다"

    echo = {"kit": key["kit"], "reviewer": "테스트", "labels": {bid: {"label": k["gold"], "note": ""} for bid, k in key["items"].items()}}
    result = human.agreement(echo, key)
    for suite in ("grounding", "resolution", "agent"):
        block = result["suites"][suite]
        assert block["labelled"] == block["sample"] and block["human_vs_reference"]["agreement"] == 1.0 and not block["disagreements"]
    flipped = {
        **echo,
        "labels": {**echo["labels"], "A01": {"label": "wrong" if key["items"]["A01"]["gold"] == "correct" else "correct", "note": "x"}},
    }
    agent = human.agreement(flipped, key)["suites"]["agent"]
    assert agent["human_vs_reference"]["agree"] == agent["sample"] - 1
    assert len(agent["grader_passed_but_human_says_wrong"]) + len(agent["grader_failed_but_human_says_correct"]) == 1
    with pytest.raises(ValueError, match="표본"):
        human.agreement({**echo, "kit": "다른표본"}, key)
