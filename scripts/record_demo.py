"""공개 데모에 싣는 '저장된 상담 기록'을 실제 모델로 만들어 낸다.

키가 없는 데모 서버에서도 실제 모델이 한 대화를 볼 수 있게 하기 위한 것이다. 각 기록 파일에는
언제·어떤 모델·어떤 프롬프트 버전·어떤 커밋·어떤 Actions 실행에서 나왔는지를 함께 적는다.
고객 메시지는 아래에 정해 둔 글이고, 상담원 메시지도 사람이 아니라 이 스크립트가 넣은 글이다
(기록 안에 그렇게 표시된다).

    ANTHROPIC_API_KEY=... python scripts/record_demo.py --out eval_results/recordings
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from batondesk.evals.data import EVAL_NOW
from batondesk.service import SupportService
from batondesk.settings import Settings
from batondesk.store.db import Database

SCRIPTED_AGENT = "데모 상담원(스크립트)"

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "01-policy-with-sources",
        "title": "정책 문의 — 근거 문서와 함께 답변",
        "customer": "C-1001",
        "turns": ["무료배송으로 받은 상품인데 단순변심으로 반품할 수 있나요?", "그럼 배송비는 얼마 내야 해요?", "아 그렇군요, 감사합니다."],
    },
    {
        "id": "02-order-lookup",
        "title": "주문 조회 — 예시 주문 시스템 도구 호출",
        "customer": "C-1001",
        "turns": ["BM-24012 주문은 지금 어디쯤 있나요?"],
    },
    {
        "id": "03-return-with-confirmation",
        "title": "반품 접수 — 금액 안내 후 고객 동의를 받고 실행",
        "customer": "C-1002",
        "turns": ["BM-24021 이어폰 단순변심으로 반품할게요.", "네, 접수해 주세요."],
    },
    {
        "id": "04-document-conflict",
        "title": "문서끼리 내용이 다를 때 — 현행 정책 기준으로 답변",
        "customer": "C-1001",
        "turns": ["35,000원짜리 하나 주문하려는데 무료배송 맞죠? FAQ에는 3만 원 이상이면 무료라고 돼 있던데요."],
    },
    {
        "id": "05-no-answer-in-documents",
        "title": "문서에 답이 없는 문의 — 지어내지 않고 멈춤",
        "customer": "C-1001",
        "turns": ["해외로도 배송해 주시나요?"],
    },
    {
        "id": "06-order-system-down",
        "title": "주문 시스템 장애 — 추측하지 않고 상담원에게 인계",
        "customer": "C-1001",
        "fault": "error",
        "turns": ["BM-24011 주문 지금 어떤 상태예요?"],
        "agent": {
            "reply": "안녕하세요, 상담원입니다. 시스템 점검이 끝나 확인해 보니 BM-24011 주문은 11월 17일에 배송이 완료되었습니다.",
            "note": "주문 시스템 복구 후 직접 조회해 안내",
            "return_to_ai": False,
        },
    },
    {
        "id": "07-policy-exception",
        "title": "정책 밖 요청 — 사람의 판단이 필요해 인계",
        "customer": "C-1003",
        "turns": ["BM-24031 반품 기간이 하루 지났는데 한 번만 봐주시면 안 될까요?"],
        "agent": {
            "reply": "안녕하세요, 상담원입니다. 도서산간 배송으로 수령이 늦어진 점을 감안해 이번 한 번은 반품을 접수해 드리겠습니다.",
            "note": "수령 지연 사유로 1회 예외 승인",
            "return_to_ai": False,
        },
    },
    {
        "id": "08-needs-human-review",
        "title": "금액을 시스템이 정할 수 없는 건 — 상담원 확인으로 인계",
        "customer": "C-1001",
        "turns": ["BM-24016 귤이 몇 개 터져서 왔어요. 그만큼만 환불해 주세요."],
    },
]


async def record(out: Path) -> int:
    settings = Settings()
    settings.daily_usd_cap = 1e9
    service = SupportService(settings, db=Database(":memory:"))
    if service.engine != "live":
        raise SystemExit("ANTHROPIC_API_KEY 가 없어 실제 모델 기록을 만들 수 없습니다.")
    service.resolution_inline = True
    out.mkdir(parents=True, exist_ok=True)
    run_url = None
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    prompt = service.prompts.get("agent", settings.agent_prompt)
    for scenario in SCENARIOS:
        sandbox_id = f"record-{scenario['id']}"
        sandbox = service.commerce_store.reset(sandbox_id, fixed_now=EVAL_NOW)
        sandbox.fault = scenario.get("fault", "none")
        conversation = service.create_conversation(scenario["customer"], channel="web", sandbox_id=sandbox_id)
        cid = conversation["id"]
        for text in scenario["turns"]:
            await service.handle_customer_message(cid, text)
        agent = scenario.get("agent")
        handoff = service.repo.open_handoff_for(cid)
        if agent and handoff:
            service.agent_reply(handoff["id"], SCRIPTED_AGENT, agent["reply"])
            service.resolve_handoff(handoff["id"], SCRIPTED_AGENT, agent["note"], agent["return_to_ai"])
        await service.update_resolution(cid)
        view = service.conversation_view(cid, with_trace=True)
        data = {
            "title": scenario["title"],
            "provenance": {
                "kind": "실제 모델 호출 기록",
                "recorded_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
                "agent_model": settings.agent_model,
                "judge_model": settings.judge_model,
                "agent_prompt": prompt.id,
                "agent_prompt_sha256": prompt.sha256[:12],
                "git_sha": settings.resolved_git_sha,
                "run_url": run_url,
                "example_clock": EVAL_NOW.isoformat(),
                "order_system_fault": scenario.get("fault", "none"),
                "customer_messages": "스크립트에 정해 둔 글",
                "agent_messages": "사람이 아니라 스크립트가 넣은 글" if agent else "없음",
            },
            "export": json.loads(json.dumps(service.export_conversation(cid), default=str)),
        }
        (out / f"{scenario['id']}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        last_ai = next((m["text"] for m in reversed(view["messages"]) if m["role"] == "ai"), "")
        print(
            f"{scenario['id']}: 상태 {view['status']} · 해결 판정 {(view['resolution'] or {}).get('status')} · 마지막 AI 응답: {last_ai[:70]}"
        )
    await service.aclose()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="eval_results/recordings")
    raise SystemExit(asyncio.run(record(Path(parser.parse_args().out))))
