"""HTTP API로 본 상담 흐름: 문의 → 응답 → 인계 → 상담원 응대 → 종료, 그리고 운영 지표."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from batondesk.api.app import create_app
from batondesk.llm.scripted import QueueLLM, agent_payload, fake_final, fake_respond, fake_response
from batondesk.service import SupportService
from batondesk.settings import Settings

from .conftest import ACTIVE

SUPPORTED = {"claims": [{"claim": "c", "label": "supported", "evidence_ids": ["K1"], "reason": "일치"}]}
RESOLVED = {"status": "resolved_assumed", "customer_signal": "none", "reason": "답변 후 종료"}


def _start(client: TestClient, customer: str = "C-1001") -> str:
    response = client.post("/v1/conversations", json={"customer_id": customer})
    assert response.status_code == 200
    return str(response.json()["id"])


def _say(client: TestClient, cid: str, text: str) -> dict:
    response = client.post(f"/v1/conversations/{cid}/messages", json={"text": text})
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_answer_with_sources_and_trace(client: TestClient, llm: QueueLLM) -> None:
    cid = _start(client)
    llm.push(
        fake_respond(agent_payload("처음에 배송비를 냈다면 3,000원입니다.", citations=["K1"])), fake_final(SUPPORTED), fake_final(RESOLVED)
    )
    result = _say(client, cid, "반품 배송비 얼마예요?")
    message = result["message"]
    assert message["role"] == "ai" and message["payload"]["action"] == "answer"
    source = message["payload"]["citations"][0]
    assert source["id"] == "K1" and source["meta"]["doc_id"] == "return-policy" and source["text"]
    assert result["turn"]["grounding_verdict"] == "grounded" and result["turn"]["llm_calls"] == 2
    assert result["handoff"] is None

    view = client.get(f"/v1/conversations/{cid}?trace=true").json()
    assert view["resolution"]["status"] == "resolved_assumed" and view["resolution"]["source"] == "judge"
    turn = view["turns"][0]
    assert turn["prompt_id"] == ACTIVE["agent"] and len(turn["prompt_sha"]) == 12
    assert [s["type"] for s in turn["detail"]["steps"]] == ["retrieval", "llm", "llm", "grounding"]
    assert client.get(f"/v1/conversations/{cid}?after_seq=1").json()["messages"][0]["role"] == "ai"


def test_validation_and_missing_resources(client: TestClient) -> None:
    assert client.post("/v1/conversations", json={"customer_id": "C-0000"}).json()["error"]["code"] == "customer_not_found"
    assert client.get("/v1/conversations/nope").status_code == 404
    cid = _start(client)
    assert client.post(f"/v1/conversations/{cid}/messages", json={"text": "   "}).json()["error"]["code"] == "empty_message"
    assert client.post(f"/v1/conversations/{cid}/messages", json={"text": "가" * 601}).json()["error"]["code"] == "message_too_long"
    assert client.get("/v1/handoffs/nope").status_code == 404


def test_handoff_lifecycle(client: TestClient, llm: QueueLLM) -> None:
    cid = _start(client)
    llm.push(
        fake_response(tool_calls=[("get_order", {"order_id": "BM-24031"})]),
        fake_respond(
            agent_payload(
                "반품 기간이 지나 제가 처리해 드릴 수 없는 건이라 상담원에게 연결해 드리겠습니다.",
                action="handoff", handoff_reason="policy_exception",
                handoff_summary="고객 요청: 기간이 지난 주문의 반품. 확인: 주문을 찾지 못함. 권하는 조치: 주문 확인 후 예외 여부 판단.",
            )
        ),
    )  # fmt: skip
    result = _say(client, cid, "BM-24031 하루 지났는데 반품해 주세요")
    handoff = result["handoff"]
    assert handoff["reason"] == "policy_exception" and handoff["status"] == "waiting" and handoff["source"] == "model"
    assert result["conversation"]["status"] == "handed_off"
    assert result["conversation"]["messages"][-1]["payload"]["kind"] == "handoff_opened"
    assert result["conversation"]["resolution"]["status"] == "handed_off"

    queue = client.get("/v1/handoffs?status=waiting").json()["handoffs"]
    assert queue[0]["id"] == handoff["id"] and queue[0]["reason_label"] == "정책 밖 예외·보상 요구"
    assert queue[0]["customer_question"] == "BM-24031 하루 지났는데 반품해 주세요"

    detail = client.get(f"/v1/handoffs/{handoff['id']}").json()
    snapshot = detail["snapshot"]
    assert "예외 여부 판단" in detail["summary"]
    assert snapshot["tool_calls"][0]["name"] == "get_order" and snapshot["tool_calls"][0]["status"] == "not_found"
    assert any(e["kind"] == "tool" for e in snapshot["evidence"]) and any(e["kind"] == "kb" for e in snapshot["evidence"])
    assert detail["conversation"]["turns"][0]["handoff_reason"] == "policy_exception"

    standby = _say(client, cid, "아직인가요?")
    assert standby["standby"] is True and not llm.requests[2:], "인계된 뒤에는 AI가 답하지 않는다"

    replied = client.post(f"/v1/handoffs/{handoff['id']}/reply", json={"agent_name": "민지", "text": "확인해 보겠습니다."}).json()
    assert replied["status"] == "assigned" and replied["assigned_to"] == "민지"
    roles = [m["role"] for m in replied["conversation"]["messages"]]
    assert roles[-2:] == ["system", "agent"]

    done = client.post(
        f"/v1/handoffs/{handoff['id']}/resolve", json={"agent_name": "민지", "note": "예외 승인", "return_to_ai": False}
    ).json()
    assert done["status"] == "resolved" and done["conversation"]["status"] == "closed"
    assert client.post(f"/v1/conversations/{cid}/messages", json={"text": "감사합니다"}).json()["error"]["code"] == "conversation_closed"
    assert client.post(f"/v1/handoffs/{handoff['id']}/reply", json={"text": "추가"}).json()["error"]["code"] == "handoff_resolved"


def test_agent_can_return_the_conversation_to_ai(client: TestClient, llm: QueueLLM) -> None:
    cid = _start(client)
    handoff = client.post(f"/v1/conversations/{cid}/handoff").json()["handoff"]
    assert handoff["reason"] == "customer_request" and handoff["source"] == "customer"
    client.post(f"/v1/handoffs/{handoff['id']}/resolve", json={"note": "", "return_to_ai": True})
    llm.push(fake_respond(agent_payload("7일 이내에 신청하실 수 있습니다.", citations=["K1"])), fake_final(SUPPORTED))
    result = _say(client, cid, "반품 언제까지 돼요?")
    assert result["standby"] is False and result["conversation"]["status"] == "open"
    history = llm.requests[0].messages
    assert history[0]["content"].startswith("<고객_메시지>"), "버튼으로 남긴 고객 요청도 대화 기록에 있다"
    assert result["conversation"]["resolution"]["status"] == "handed_off", "한 번 인계된 대화는 AI 해결로 세지 않는다"


def test_feedback_overrides_the_judge(client: TestClient, llm: QueueLLM) -> None:
    cid = _start(client)
    llm.push(fake_respond(agent_payload("7일 이내에 신청하실 수 있습니다.", citations=["K1"])), fake_final(SUPPORTED), fake_final(RESOLVED))
    _say(client, cid, "반품 언제까지 돼요?")
    no = client.post(f"/v1/conversations/{cid}/feedback", json={"resolved": False}).json()
    assert no["resolution"]["status"] == "unresolved" and no["resolution"]["source"] == "customer_feedback"
    yes = client.post(f"/v1/conversations/{cid}/feedback", json={"resolved": True}).json()
    assert yes["resolution"]["status"] == "resolved_confirmed"


def test_metrics_follow_the_definitions(client: TestClient, llm: QueueLLM) -> None:
    a, b, c = _start(client), _start(client, "C-1002"), _start(client, "C-1003")
    llm.push(fake_respond(agent_payload("7일 이내입니다.", citations=["K1"])), fake_final(SUPPORTED), fake_final(RESOLVED))
    _say(client, a, "반품 언제까지 돼요?")
    llm.push(
        fake_respond(agent_payload("반품은 14일 이내입니다.", citations=["K1"])),
        fake_final({"claims": [{"claim": "14일", "label": "contradicted", "evidence_ids": ["K1"], "reason": "7일"}]}),
        fake_respond(agent_payload("7일 이내입니다.", citations=["K1"])), fake_final(SUPPORTED),
        fake_final({"status": "unresolved", "customer_signal": "negative", "reason": "불만"}),
    )  # fmt: skip
    _say(client, b, "반품 언제까지 돼요? 지난번엔 14일이라던데")
    client.post(f"/v1/conversations/{c}/handoff")
    _start(client)  # 아무 말도 하지 않은 대화

    m = client.get("/v1/metrics/summary").json()
    assert m["conversations"]["total"] == 4 and m["conversations"]["ai_involved"] == 3
    assert m["resolution"]["denominator"] == 3 and m["resolution"]["rate"] == round(1 / 3, 4)
    assert m["handoff"]["conversations"] == 1 and m["handoff"]["rate"] == round(1 / 3, 4) and m["handoff"]["waiting"] == 1
    assert m["grounding"]["blocked_drafts"] == 1 and m["grounding"]["turns_with_revision"] == 1
    assert m["turns"]["total"] == 2 and m["turns"]["latency_ms"]["p50"] is not None
    assert m["llm"]["calls"] == 6 and m["llm"]["by_prompt"] == {ACTIVE["agent"]: 2}
    assert m["flagged"][0]["conversation_id"] == b and "14일" in m["flagged"][0]["blocked"][0]["message"]
    assert client.get("/v1/metrics/summary?source=recorded").json()["conversations"]["total"] == 0
    events = client.get("/v1/events?type=grounding.blocked").json()["events"]
    assert len(events) == 1 and events[0]["conversation_id"] == b


def test_kb_issue_and_tool_failure_are_surfaced_to_ops(client: TestClient, llm: QueueLLM) -> None:
    cid = _start(client)
    client.put("/v1/demo/fault", json={"mode": "error"})
    llm.push(
        fake_response(tool_calls=[("get_order", {"order_id": "BM-24011"})]),
        fake_respond(
            agent_payload(
                "주문 확인이 어려워 상담원에게 연결합니다.",
                action="handoff",
                handoff_reason="tool_failure",
                handoff_summary="조회 실패",
                kb_issue="FAQ의 무료배송 기준(30,000원)이 정책(50,000원)과 다릅니다.",
            )
        ),
    )
    result = _say(client, cid, "BM-24011 상태요")
    assert result["turn"]["tool_errors"] == 1
    m = client.get("/v1/metrics/summary").json()
    assert m["tools"]["failures"] == 1 and m["tools"]["by_status"] == {"unavailable": 1}
    assert set(m["flagged"][0]["reasons"]) == {"주문 시스템 호출 실패", "지식 문서 점검 요청"}
    assert client.get("/v1/events?type=kb.issue").json()["events"][0]["data"]["note"].startswith("FAQ")
    client.put("/v1/demo/fault", json={"mode": "none"})
    assert client.get("/v1/demo/sandbox").json()["fault"] == "none"


def test_stream_endpoint_reports_progress_then_result(client: TestClient, llm: QueueLLM) -> None:
    cid = _start(client)
    llm.push(
        fake_response(tool_calls=[("track_shipment", {"order_id": "BM-24012"})]),
        fake_respond(agent_payload("BM-24012 주문은 서울 송파 터미널에 있습니다.", citations=["T1"])), fake_final(SUPPORTED), fake_final(RESOLVED),
    )  # fmt: skip
    with client.stream("POST", f"/v1/conversations/{cid}/messages/stream", json={"text": "BM-24012 어디예요"}) as stream:
        raw = "".join(stream.iter_text())
    events = [(block.split("\n")[0][7:], json.loads(block.split("\n")[1][6:])) for block in raw.strip().split("\n\n")]
    assert [name for name, _ in events] == ["progress", "progress", "progress", "progress", "result"]
    assert events[1][1]["label"] == "확인 중: track_shipment"
    assert events[-1][1]["message"]["payload"]["citations"][0]["kind"] == "tool"

    with client.stream("POST", "/v1/conversations/nope/messages/stream", json={"text": "x"}) as stream:
        assert "event: error" in "".join(stream.iter_text())


def test_check_apis_work_without_a_conversation(client: TestClient, llm: QueueLLM) -> None:
    body = {
        "answer": "반품은 14일 이내에 가능하고 배송비는 5,000원입니다.",
        "evidence": [{"id": "K1", "text": "받은 날부터 7일 이내에 반품을 신청할 수 있습니다. 반품 배송비는 3,000원입니다."}],
        "use_llm": False,
    }
    rules = client.post("/v1/checks/grounding", json=body).json()
    assert rules["mode"] == "rules_only" and rules["verdict"] == "ungrounded"
    assert {f["surface"] for f in rules["fact_flags"]} == {"14일", "5,000원"}

    llm.push(fake_final({"claims": [{"claim": "14일 이내", "label": "contradicted", "evidence_ids": ["K1"], "reason": "7일"}]}))
    judged = client.post("/v1/checks/grounding", json={**body, "use_llm": True}).json()
    assert judged["mode"] == "llm+rules" and judged["contradicted"] == 1 and judged["checker"]["prompt"] == ACTIVE["grounding"]

    llm.push(fake_final({"status": "unresolved", "customer_signal": "negative", "reason": "고객이 반박"}))
    resolution = client.post("/v1/checks/resolution", json={"messages": [{"role": "고객", "text": "아닌데요"}]}).json()
    assert resolution["status"] == "unresolved" and resolution["resolved"] is False and resolution["counted_in_rate"] is True
    handed = client.post("/v1/checks/resolution", json={"messages": [], "handed_off": True}).json()
    assert handed["status"] == "handed_off"


def test_knowledge_and_ops_endpoints(client: TestClient) -> None:
    hits = client.post("/v1/kb/search", json={"query": "제주 추가 배송비", "k": 3}).json()["hits"]
    assert hits[0]["doc_id"] == "shipping-remote" and hits[0]["validity"].startswith("현행")
    docs = client.get("/v1/kb/documents").json()["documents"]
    assert {d["doc_id"]: d["in_effect"] for d in docs}["notice-2025-yearend-returns"] is False
    assert client.get("/v1/kb/documents/return-policy").json()["sections"][0]["chunk_id"] == "return-policy#1"
    release = client.get("/v1/release").json()
    assert release["prompts"]["agent"]["id"] == ACTIVE["agent"] and release["engine"]["engine"] == "scripted"
    prompt_list = client.get("/v1/prompts").json()
    assert prompt_list["active"]["agent"] == ACTIVE["agent"]
    assert "근거" in client.get("/v1/prompts/agent-v1").json()["system"]
    customers = client.get("/v1/demo/customers").json()["customers"]
    assert customers[0]["orders"][0]["order_id"].startswith("BM-")
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/example-commerce/orders/BM-24011", headers={"X-Customer-Id": "C-1001"}).headers["x-example-system"] == "true"
    for page in ("/", "/chat", "/desk", "/ops"):
        assert client.get(page).status_code == 200


def test_webhook_channel_keeps_one_conversation_per_user(client: TestClient, llm: QueueLLM) -> None:
    llm.push(fake_respond(agent_payload("7일 이내입니다.", citations=["K1"])), fake_final(SUPPORTED), fake_final(RESOLVED))
    first = client.post(
        "/v1/channels/webhook", json={"channel": "kakao", "external_user_key": "u-1", "customer_id": "C-1001", "text": "반품 기한?"}
    ).json()
    assert first["reply"] == "7일 이내입니다." and first["sources"][0]["id"] == "K1" and first["handoff"] is False
    llm.push(fake_respond(agent_payload("주문번호를 알려 주세요.", action="ask")), fake_final(RESOLVED))
    second = client.post(
        "/v1/channels/webhook", json={"channel": "kakao", "external_user_key": "u-1", "customer_id": "C-1001", "text": "그럼 접수해 주세요"}
    ).json()
    assert second["conversation_id"] == first["conversation_id"] and second["action"] == "ask"


def test_server_without_a_model_refuses_new_conversations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(db_path=tmp_path / "off.sqlite3", llm_mode="auto", _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings)) as off:
        assert off.get("/v1/release").json()["engine"]["engine"] == "off"
        refused = off.post("/v1/conversations", json={"customer_id": "C-1001"})
        assert refused.status_code == 503 and refused.json()["error"]["code"] == "llm_unavailable"
        assert off.post("/v1/kb/search", json={"query": "반품"}).status_code == 200


async def test_recordings_are_read_only_and_reported_separately(service: SupportService, llm: QueueLLM) -> None:
    conv = service.create_conversation("C-1001")
    llm.push(fake_respond(agent_payload("7일 이내입니다.", citations=["K1"])), fake_final(SUPPORTED), fake_final(RESOLVED))
    await service.handle_customer_message(conv["id"], "반품 기한?")
    export = json.loads(json.dumps(service.export_conversation(conv["id"]), default=str))
    rid = service.import_recording(export, recording_id="demo-01", title="반품 기한 문의", provenance={"model": "m", "run": "r"})
    assert service.import_recording(export, recording_id="demo-01", title="x", provenance={}) == rid, "두 번 실어도 한 건"

    view = service.conversation_view(rid, with_trace=True)
    assert view["source"] == "recorded" and view["meta"]["title"] == "반품 기한 문의"
    assert [m["role"] for m in view["messages"]] == ["customer", "ai"] and view["turns"][0]["prompt_id"] == ACTIVE["agent"]
    with pytest.raises(Exception, match="저장된 상담 기록"):
        await service.handle_customer_message(rid, "더 물어볼게요")
    from batondesk.obs.metrics import compute_metrics

    assert compute_metrics(service.repo, "recorded")["conversations"]["total"] == 1
    assert compute_metrics(service.repo, "scripted")["conversations"]["total"] == 1


async def test_turn_cap(service: SupportService, llm: QueueLLM) -> None:
    service.settings.max_customer_turns = 1
    conv = service.create_conversation("C-1001")
    llm.push(fake_respond(agent_payload("주문번호를 알려 주세요.", action="ask")), fake_final(RESOLVED))
    await service.handle_customer_message(conv["id"], "취소요")
    with pytest.raises(Exception, match="대화가 길어졌습니다"):
        await service.handle_customer_message(conv["id"], "BM-24022")
