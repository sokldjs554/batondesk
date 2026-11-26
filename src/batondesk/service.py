"""상담 서비스: 대화 한 건의 생애(문의 → AI 응답 → 인계 → 상담원 응대 → 종료)를 다룬다.

API 계층은 이 클래스만 부르고, 이 클래스는 에이전트 그래프·판정기·저장소를 묶는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from pathlib import Path
from typing import Any

import httpx

from batondesk.agent.evidence import EvidencePool
from batondesk.agent.graph import HistoryItem, ProgressFn, TurnConfig, TurnInput, TurnResult, build_agent_graph, run_turn
from batondesk.agent.protocol import HANDOFF_REASON_LABEL
from batondesk.checks.grounding import GroundingChecker
from batondesk.checks.resolution import ResolutionChecker, ResolutionResult
from batondesk.commerce.app import create_commerce_app
from batondesk.commerce.store import CommerceStore
from batondesk.kb.retriever import KnowledgeBase
from batondesk.llm.base import LLMClient, LLMError, LLMRequest, LLMResponse
from batondesk.obs.logging import log_event
from batondesk.prompts.registry import PromptRegistry
from batondesk.settings import Settings
from batondesk.store.db import Database, utcnow
from batondesk.store.repo import Repo
from batondesk.tools.client import CommerceClient
from batondesk.tools.executor import PendingAction, ToolExecutor

ROLE_LABEL = {"customer": "고객", "ai": "AI", "agent": "상담원"}
STANDBY_NOTICE = "상담원이 대화를 이어받았습니다. 보내신 메시지는 상담원에게 전달되며, AI는 답하지 않습니다."


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class MeteredLLM:
    """모델 호출마다 추정 비용을 하루 한도 장부에 더한다."""

    def __init__(self, inner: LLMClient, repo: Repo) -> None:
        self.inner = inner
        self.repo = repo
        self.engine = inner.engine

    async def complete(self, request: LLMRequest) -> LLMResponse:
        response = await self.inner.complete(request)
        if response.engine == "anthropic":
            self.repo.add_spend(response.cost_usd or 0.0)
        return response


class SupportService:
    def __init__(self, settings: Settings, *, llm: LLMClient | None = None, db: Database | None = None) -> None:
        self.settings = settings
        self.db = db or Database(settings.db_path)
        self.repo = Repo(self.db)
        self.kb = KnowledgeBase.from_dir(settings.kb_dir)
        self.prompts = PromptRegistry(settings.prompts_dir)
        self.commerce_store = CommerceStore(settings.data_dir / "commerce" / "orders.json")
        self.commerce_app = create_commerce_app(self.commerce_store)
        if settings.commerce_url:
            self._http = httpx.AsyncClient(base_url=settings.commerce_url)
        else:
            self._http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.commerce_app), base_url="http://example-commerce")
        self.commerce = CommerceClient(self._http, timeout_s=settings.tool_timeout_s, retries=settings.tool_retries)
        self.executor = ToolExecutor(self.kb, self.commerce)

        raw_llm = llm
        if raw_llm is None:
            mode = settings.effective_llm_mode
            if mode == "live":
                from batondesk.llm.anthropic_llm import AnthropicLLM

                raw_llm = AnthropicLLM(timeout_s=settings.llm_timeout_s, max_attempts=settings.llm_max_attempts)
            elif mode == "scripted":
                from batondesk.llm.scripted import RuleLLM

                raw_llm = RuleLLM()
        self.llm: LLMClient | None = MeteredLLM(raw_llm, self.repo) if raw_llm is not None else None
        self._graphs: dict[tuple[str, str, str], Any] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self.resolution_inline = False  # 테스트·평가에서는 True 로 두어 판정이 끝난 뒤 응답한다

    async def aclose(self) -> None:
        for task in list(self._background):
            with contextlib.suppress(Exception):
                await task
        await self._http.aclose()
        self.db.close()

    # ---- 상태 ----------------------------------------------------------------

    @property
    def engine(self) -> str:
        """live(실제 모델) | rule-engine(LLM 아님) | off | scripted(테스트 대역)"""
        if self.llm is None:
            return "off"
        return {"anthropic": "live"}.get(self.llm.engine, self.llm.engine)

    def engine_info(self) -> dict[str, Any]:
        spend = self.repo.spend_today()
        engine = self.engine
        labels = {
            "live": f"실제 모델 호출 ({self.settings.agent_model})",
            "rule-engine": "규칙 엔진 (LLM 아님, 개발·테스트용)",
            "scripted": "테스트 대역 (LLM 아님)",
            "off": "모델 호출 꺼짐 — 저장된 상담 기록만 볼 수 있습니다",
        }
        return {
            "engine": engine,
            "label": labels.get(engine, engine),
            "agent_model": self.settings.agent_model if engine == "live" else None,
            "judge_model": self.settings.judge_model if engine == "live" else None,
            "budget": {
                "daily_usd_cap": self.settings.daily_usd_cap,
                "spent_usd_today": round(spend["usd"], 4),
                "llm_calls_today": spend["calls"],
                "exhausted": spend["usd"] >= self.settings.daily_usd_cap,
            },
        }

    def _graph(self) -> tuple[Any, TurnConfig]:
        assert self.llm is not None
        prompt = self.prompts.get("agent", self.settings.agent_prompt)
        grounding_prompt = self.prompts.get("grounding", self.settings.grounding_prompt)
        key = (prompt.id, self.settings.agent_model, grounding_prompt.id)
        config = TurnConfig(
            model=self.settings.agent_model,
            prompt=prompt,
            effort=self.settings.agent_effort,
            max_tokens=self.settings.agent_max_tokens,
            retrieval_k=self.settings.retrieval_k,
            max_tool_rounds=self.settings.max_tool_rounds,
            grounding_mode=self.settings.grounding_mode,
            max_revisions=self.settings.max_revisions,
        )
        if key not in self._graphs:
            checker = GroundingChecker(
                self.llm,
                grounding_prompt,
                self.settings.judge_model,
                effort=self.settings.judge_effort,
                max_tokens=self.settings.judge_max_tokens,
            )
            self._graphs[key] = build_agent_graph(self.llm, self.executor, checker, config)
        return self._graphs[key], config

    def grounding_checker(self) -> GroundingChecker:
        return GroundingChecker(
            self.llm,
            self.prompts.get("grounding", self.settings.grounding_prompt),
            self.settings.judge_model,
            effort=self.settings.judge_effort,
            max_tokens=self.settings.judge_max_tokens,
        )

    def resolution_checker(self) -> ResolutionChecker:
        return ResolutionChecker(
            self.llm,
            self.prompts.get("resolution", self.settings.resolution_prompt),
            self.settings.judge_model,
            effort=self.settings.judge_effort,
        )

    # ---- 대화 ----------------------------------------------------------------

    def customers(self) -> list[dict[str, Any]]:
        return self.commerce_store.customers

    def create_conversation(
        self, customer_id: str, *, channel: str = "web", sandbox_id: str = "demo", meta: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        customer = next((c for c in self.customers() if c["customer_id"] == customer_id), None)
        if customer is None:
            raise ServiceError(404, "customer_not_found", "예시 고객을 찾을 수 없습니다.")
        if self.engine == "off":
            raise ServiceError(503, "llm_unavailable", "이 서버에는 모델 호출이 꺼져 있습니다. 저장된 상담 기록을 확인해 주세요.")
        conv = self.repo.create_conversation(
            customer_id=customer_id,
            customer_name=customer["name"],
            channel=channel,
            sandbox_id=sandbox_id,
            source=self.engine,
            meta=meta,
        )
        self.repo.add_event("conversation.created", conv["id"], {"channel": channel, "source": self.engine})
        return self.conversation_view(conv["id"])

    def _require(self, conversation_id: str) -> dict[str, Any]:
        conv = self.repo.get_conversation(conversation_id)
        if conv is None:
            raise ServiceError(404, "conversation_not_found", "대화를 찾을 수 없습니다.")
        return conv

    async def handle_customer_message(
        self,
        conversation_id: str,
        text: str,
        *,
        on_progress: ProgressFn | None = None,
        client_key: str | None = None,
        inject_draft: str | None = None,
    ) -> dict[str, Any]:
        conv = self._require(conversation_id)
        text = text.strip()
        if not text:
            raise ServiceError(422, "empty_message", "메시지가 비어 있습니다.")
        if len(text) > self.settings.max_message_chars:
            raise ServiceError(422, "message_too_long", f"메시지는 {self.settings.max_message_chars}자까지 보낼 수 있습니다.")
        if conv["source"] == "recorded":
            raise ServiceError(409, "recorded_conversation", "저장된 상담 기록에는 메시지를 보낼 수 없습니다.")
        if conv["status"] == "closed":
            raise ServiceError(409, "conversation_closed", "종료된 대화입니다. 새 대화를 시작해 주세요.")

        if conv["status"] == "handed_off":
            message = self.repo.add_message(conversation_id, "customer", text)
            self.repo.add_event("message.customer_while_handed_off", conversation_id, {"seq": message["seq"]})
            return {"standby": True, "notice": STANDBY_NOTICE, "conversation": self.conversation_view(conversation_id)}

        if self.llm is None:
            raise ServiceError(503, "llm_unavailable", "이 서버에는 모델 호출이 꺼져 있습니다. 저장된 상담 기록을 확인해 주세요.")
        if conv["turn_count"] >= self.settings.max_customer_turns:
            raise ServiceError(409, "conversation_too_long", "대화가 길어졌습니다. 새 대화를 시작해 주세요.")
        if self.engine == "live":
            if self.repo.spend_today()["usd"] >= self.settings.daily_usd_cap:
                raise ServiceError(429, "budget_exhausted", "오늘의 데모 모델 호출 한도를 다 썼습니다. 저장된 상담 기록을 확인해 주세요.")
            if client_key and self.repo.hit_rate(client_key) > self.settings.rate_limit_per_hour:
                raise ServiceError(429, "rate_limited", "요청이 많습니다. 잠시 뒤에 다시 시도해 주세요.")

        history = [
            HistoryItem(role=m["role"], text=m["text"])
            for m in self.repo.messages(conversation_id)
            if m["role"] in ("customer", "ai", "agent")
        ]
        self.repo.add_message(conversation_id, "customer", text)
        sandbox = self.commerce_store.sandbox(conv["sandbox_id"])
        pool = EvidencePool.load(conv["evidence"])
        pending = {code: PendingAction(**data) for code, data in (conv["pending"] or {}).items()}
        turn_seq = conv["turn_count"] + 1
        turn = TurnInput(
            conversation_id=conversation_id,
            turn_seq=turn_seq,
            customer_id=conv["customer_id"],
            customer_name=conv["customer_name"],
            sandbox_id=conv["sandbox_id"],
            now=sandbox.now(),
            history=history,
            customer_text=text,
            pool=pool,
            pending=pending,
            inject_draft=inject_draft,
        )
        graph, config = self._graph()
        started_at = utcnow()
        started = time.perf_counter()
        result = await run_turn(graph, turn, on_progress)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        record = self._turn_record(result, config, started_at, latency_ms)
        citations = [e.model_dump() for cid in result.output.citations if (e := pool.get(cid)) is not None]
        payload = {
            "turn_seq": turn_seq,
            "action": result.output.action,
            "citations": citations,
            "handoff_reason": result.output.handoff_reason if result.output.action == "handoff" else None,
            "engine": result.engine,
            "grounding": result.grounding.verdict if result.grounding else None,
        }
        ai_message = self.repo.add_message(conversation_id, "ai", result.output.message, payload)
        self.repo.add_turn(conversation_id, turn_seq, record)
        self.repo.update_conversation(
            conversation_id,
            evidence=pool.dump(),
            pending={code: p.as_dict() for code, p in pending.items()},
            turn_count=turn_seq,
        )
        self._emit_turn_events(conversation_id, turn_seq, result, record)

        handoff = None
        if result.output.action == "handoff":
            handoff = self._open_handoff(
                conversation_id,
                turn_seq,
                reason=result.output.handoff_reason,
                source=result.handoff_source or "model",
                summary=result.output.handoff_summary,
                result=result,
                pool=pool,
            )
        await self._schedule_resolution(conversation_id)
        return {
            "standby": False,
            "message": ai_message,
            "handoff": handoff,
            "turn": self._turn_summary(record),
            "conversation": self.conversation_view(conversation_id),
        }

    # ---- 턴 기록 ---------------------------------------------------------------

    def _turn_record(self, result: TurnResult, config: TurnConfig, started_at: str, latency_ms: float) -> dict[str, Any]:
        llm_steps = [s for s in result.steps if s["type"] == "llm"]
        tool_steps = [s for s in result.steps if s["type"] == "tool"]
        costs = [s["cost_usd"] for s in llm_steps if s.get("cost_usd") is not None]
        stage: dict[str, float] = {"retrieval": 0.0, "llm_agent": 0.0, "llm_checks": 0.0, "tools": 0.0}
        for s in result.steps:
            if s["type"] == "retrieval":
                stage["retrieval"] += s.get("latency_ms", 0.0)
            elif s["type"] == "llm":
                stage["llm_agent" if s["purpose"] == "agent" else "llm_checks"] += s["latency_ms"]
            elif s["type"] == "tool" and s["name"] != "search_knowledge":
                stage["tools"] += s["latency_ms"]
        served = next((s["model"] for s in llm_steps if s["purpose"] == "agent"), None)
        return {
            "started_at": started_at,
            "latency_ms": latency_ms,
            "outcome": result.outcome,
            "handoff_reason": result.output.handoff_reason if result.output.action == "handoff" else None,
            "handoff_source": result.handoff_source,
            "grounding_verdict": result.grounding.verdict if result.grounding else None,
            "revisions": result.revisions,
            "blocked_count": len(result.blocked),
            "engine": result.engine,
            "model": served or config.model,
            "prompt_id": config.prompt.id,
            "prompt_sha": config.prompt.sha256[:12],
            "llm_calls": len(llm_steps),
            "input_tokens": sum(s["usage"]["input_tokens"] for s in llm_steps),
            "output_tokens": sum(s["usage"]["output_tokens"] for s in llm_steps),
            "cache_read_tokens": sum(s["usage"]["cache_read_input_tokens"] for s in llm_steps),
            "cost_usd": round(sum(costs), 6) if costs else None,
            "tool_calls": len(tool_steps),
            "tool_errors": sum(1 for s in tool_steps if s["status"] in ("timeout", "unavailable")),
            "error": result.error,
            "kb_issue": result.output.kb_issue or None,
            "detail": {
                "steps": result.steps,
                "blocked": result.blocked,
                "grounding": result.grounding.model_dump() if result.grounding else None,
                "stage_latency_ms": {k: round(v, 1) for k, v in stage.items()},
                "fresh_evidence_ids": result.fresh_evidence_ids,
                "cited": result.output.citations,
            },
        }

    @staticmethod
    def _turn_summary(record: dict[str, Any]) -> dict[str, Any]:
        keys = ("latency_ms", "outcome", "grounding_verdict", "revisions", "blocked_count", "engine", "model",
                "prompt_id", "llm_calls", "tool_calls", "tool_errors", "cost_usd", "error")  # fmt: skip
        return {k: record.get(k) for k in keys} | {"stage_latency_ms": record["detail"]["stage_latency_ms"]}

    def _emit_turn_events(self, conversation_id: str, turn_seq: int, result: TurnResult, record: dict[str, Any]) -> None:
        base = {"turn_seq": turn_seq, "engine": result.engine, "model": record["model"], "prompt": record["prompt_id"]}
        for step in result.steps:
            if step["type"] == "llm":
                data = {**base, **{k: step[k] for k in ("purpose", "latency_ms", "attempts", "usage", "cost_usd", "stop_reason")}}
                self._event("llm.call", conversation_id, data)
            elif step["type"] == "tool":
                data = {**base, **{k: step[k] for k in ("name", "status", "latency_ms", "attempts")}}
                self._event("tool.call", conversation_id, data)
            elif step["type"] == "retrieval":
                data = {**base, "query": step["query"], "hits": [h["chunk_id"] for h in step["hits"]], "latency_ms": step["latency_ms"]}
                self._event("kb.search", conversation_id, data)
            elif step["type"] in ("llm_error", "check_error"):
                self._event("error", conversation_id, {**base, **step})
        if result.blocked:
            self._event("grounding.blocked", conversation_id, {**base, "blocked": result.blocked})
        if result.output.kb_issue:
            self._event("kb.issue", conversation_id, {**base, "note": result.output.kb_issue, "cited": result.output.citations})
        self._event(
            "turn.completed",
            conversation_id,
            {**base, "outcome": record["outcome"], "latency_ms": record["latency_ms"], "grounding": record["grounding_verdict"],
             "llm_calls": record["llm_calls"], "tool_calls": record["tool_calls"], "tool_errors": record["tool_errors"],
             "cost_usd": record["cost_usd"], "error": record["error"]},
        )  # fmt: skip

    def _event(self, type_: str, conversation_id: str | None, data: dict[str, Any]) -> None:
        self.repo.add_event(type_, conversation_id, data)
        log_event(type_, conversation_id=conversation_id, **data)

    # ---- 인계 -----------------------------------------------------------------

    def _open_handoff(
        self,
        conversation_id: str,
        turn_seq: int,
        *,
        reason: str,
        source: str,
        summary: str,
        result: TurnResult | None,
        pool: EvidencePool,
    ) -> dict[str, Any]:
        messages = self.repo.messages(conversation_id)
        last_customer = next((m["text"] for m in reversed(messages) if m["role"] == "customer"), "")
        tool_steps = [s for s in (result.steps if result else []) if s["type"] == "tool"]
        snapshot = {
            "customer_question": last_customer,
            "reason_label": HANDOFF_REASON_LABEL.get(reason, reason),
            "evidence": pool.dump(),
            "cited": result.output.citations if result else [],
            "tool_calls": [{k: s.get(k) for k in ("name", "args", "status", "latency_ms", "attempts")} for s in tool_steps],
            "blocked_drafts": result.blocked if result else [],
            "grounding_problems": result.grounding.problems if result and result.grounding else [],
            "message_seq_at_handoff": messages[-1]["seq"] if messages else 0,
        }
        handoff = self.repo.create_handoff(
            conversation_id=conversation_id, turn_seq=turn_seq, reason=reason, source=source, summary=summary, snapshot=snapshot
        )
        self.repo.update_conversation(conversation_id, status="handed_off")
        self.repo.add_message(
            conversation_id,
            "system",
            "상담원에게 대화를 넘겼습니다. 대화 내용과 확인한 근거가 함께 전달되었습니다.",
            {"kind": "handoff_opened", "handoff_id": handoff["id"], "reason": reason, "reason_label": snapshot["reason_label"]},
        )
        self._event(
            "handoff.created", conversation_id, {"handoff_id": handoff["id"], "reason": reason, "source": source, "turn_seq": turn_seq}
        )
        return handoff

    async def request_handoff(self, conversation_id: str) -> dict[str, Any]:
        """고객이 직접 상담원 연결 버튼을 누른 경우."""
        conv = self._require(conversation_id)
        if conv["source"] == "recorded":
            raise ServiceError(409, "recorded_conversation", "저장된 상담 기록입니다.")
        if conv["status"] == "handed_off":
            return {"handoff": self.repo.open_handoff_for(conversation_id), "conversation": self.conversation_view(conversation_id)}
        if conv["status"] == "closed":
            raise ServiceError(409, "conversation_closed", "종료된 대화입니다.")
        messages = self.repo.messages(conversation_id)
        recent = [f"{ROLE_LABEL[m['role']]}: {m['text']}" for m in messages if m["role"] in ROLE_LABEL][-4:]
        summary = "고객이 상담원 연결 버튼을 눌렀습니다(AI 요약 없음).\n최근 대화:\n" + "\n".join(recent or ["(대화 없음)"])
        self.repo.add_message(conversation_id, "customer", "상담원과 이야기하고 싶어요.", {"kind": "handoff_button"})
        handoff = self._open_handoff(
            conversation_id, conv["turn_count"], reason="customer_request", source="customer", summary=summary,
            result=None, pool=EvidencePool.load(conv["evidence"]),
        )  # fmt: skip
        await self._schedule_resolution(conversation_id)
        return {"handoff": handoff, "conversation": self.conversation_view(conversation_id)}

    def _require_handoff(self, handoff_id: str) -> dict[str, Any]:
        handoff = self.repo.get_handoff(handoff_id)
        if handoff is None:
            raise ServiceError(404, "handoff_not_found", "인계 건을 찾을 수 없습니다.")
        conv = self._require(handoff["conversation_id"])
        if conv["source"] == "recorded":
            raise ServiceError(409, "recorded_conversation", "저장된 상담 기록은 바꿀 수 없습니다.")
        return handoff

    def claim_handoff(self, handoff_id: str, agent_name: str) -> dict[str, Any]:
        handoff = self._require_handoff(handoff_id)
        if handoff["status"] == "resolved":
            raise ServiceError(409, "handoff_resolved", "이미 종료된 인계 건입니다.")
        if handoff["status"] == "waiting":
            self.repo.update_handoff(handoff_id, status="assigned", assigned_to=agent_name, assigned_at=utcnow())
            self.repo.add_message(
                handoff["conversation_id"], "system", f"상담원 {agent_name} 님이 대화를 확인하고 있습니다.",
                {"kind": "handoff_assigned", "handoff_id": handoff_id},
            )  # fmt: skip
            self._event("handoff.assigned", handoff["conversation_id"], {"handoff_id": handoff_id, "agent": agent_name})
        return self.handoff_view(handoff_id)

    def agent_reply(self, handoff_id: str, agent_name: str, text: str) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise ServiceError(422, "empty_message", "메시지가 비어 있습니다.")
        handoff = self._require_handoff(handoff_id)
        if handoff["status"] == "resolved":
            raise ServiceError(409, "handoff_resolved", "이미 종료된 인계 건입니다.")
        if handoff["status"] == "waiting":
            self.claim_handoff(handoff_id, agent_name)
        self.repo.add_message(handoff["conversation_id"], "agent", text, {"agent": agent_name, "handoff_id": handoff_id})
        self._event("handoff.agent_reply", handoff["conversation_id"], {"handoff_id": handoff_id, "agent": agent_name})
        return self.handoff_view(handoff_id)

    def resolve_handoff(self, handoff_id: str, agent_name: str, note: str, return_to_ai: bool) -> dict[str, Any]:
        handoff = self._require_handoff(handoff_id)
        if handoff["status"] == "resolved":
            return self.handoff_view(handoff_id)
        self.repo.update_handoff(
            handoff_id, status="resolved", resolved_at=utcnow(), resolution_note=note.strip(),
            assigned_to=handoff["assigned_to"] or agent_name,
        )  # fmt: skip
        conversation_id = handoff["conversation_id"]
        if return_to_ai:
            self.repo.update_conversation(conversation_id, status="open")
            notice = "상담원 응대가 끝났습니다. 이어지는 문의는 다시 AI가 답합니다."
        else:
            self.repo.update_conversation(conversation_id, status="closed")
            notice = "상담원이 상담을 종료했습니다. 이용해 주셔서 감사합니다."
        self.repo.add_message(
            conversation_id, "system", notice, {"kind": "handoff_resolved", "handoff_id": handoff_id, "return_to_ai": return_to_ai}
        )
        self._event("handoff.resolved", conversation_id, {"handoff_id": handoff_id, "agent": agent_name, "return_to_ai": return_to_ai})
        return self.handoff_view(handoff_id)

    # ---- 해결 여부 --------------------------------------------------------------

    async def feedback(self, conversation_id: str, resolved: bool) -> dict[str, Any]:
        conv = self._require(conversation_id)
        if conv["source"] == "recorded":
            raise ServiceError(409, "recorded_conversation", "저장된 상담 기록입니다.")
        value = "resolved" if resolved else "not_resolved"
        self.repo.update_conversation(conversation_id, feedback=value)
        self._event("feedback", conversation_id, {"value": value})
        await self.update_resolution(conversation_id)
        return self.conversation_view(conversation_id)

    async def _schedule_resolution(self, conversation_id: str) -> None:
        if not self.settings.resolution_check:
            return
        if self.resolution_inline:
            await self.update_resolution(conversation_id)
            return
        task = asyncio.create_task(self.update_resolution(conversation_id))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def transcript(self, conversation_id: str) -> list[dict[str, str]]:
        return [{"role": ROLE_LABEL[m["role"]], "text": m["text"]} for m in self.repo.messages(conversation_id) if m["role"] in ROLE_LABEL]

    async def update_resolution(self, conversation_id: str) -> ResolutionResult:
        conv = self._require(conversation_id)
        handed_off = self.repo.latest_handoff_for(conversation_id) is not None
        source = "judge"
        if handed_off:
            result, _ = await self.resolution_checker().check([], handed_off=True)
            source = "rule"
        elif conv["feedback"] == "resolved":
            result = ResolutionResult(status="resolved_confirmed", customer_signal="positive", reason="고객이 '해결됐어요'를 눌렀습니다.")
            source = "customer_feedback"
        elif conv["feedback"] == "not_resolved":
            result = ResolutionResult(status="unresolved", customer_signal="negative", reason="고객이 '아직이에요'를 눌렀습니다.")
            source = "customer_feedback"
        else:
            try:
                result, steps = await self.resolution_checker().check(self.transcript(conversation_id))
            except LLMError as exc:  # 방어: check() 가 잡지 못한 경우
                result, steps = ResolutionResult(status="unknown", reason=str(exc)[:200]), []
            for step in steps:
                if step["type"] == "llm":
                    self._event(
                        "llm.call",
                        conversation_id,
                        {k: step[k] for k in ("purpose", "latency_ms", "attempts", "usage", "cost_usd", "model")},
                    )
        stored = {**result.model_dump(), "source": source, "updated_at": utcnow()}
        self.repo.update_conversation(conversation_id, resolution=stored)
        return result

    # ---- 조회용 모습 -------------------------------------------------------------

    def conversation_view(self, conversation_id: str, *, after_seq: int = 0, with_trace: bool = False) -> dict[str, Any]:
        conv = self._require(conversation_id)
        handoff = self.repo.latest_handoff_for(conversation_id)
        view: dict[str, Any] = {
            "id": conv["id"],
            "customer_id": conv["customer_id"],
            "customer_name": conv["customer_name"],
            "channel": conv["channel"],
            "status": conv["status"],
            "source": conv["source"],
            "created_at": conv["created_at"],
            "updated_at": conv["updated_at"],
            "turn_count": conv["turn_count"],
            "feedback": conv["feedback"],
            "resolution": conv["resolution"],
            "handoff": _handoff_brief(handoff),
            "messages": self.repo.messages(conversation_id, after_seq=after_seq),
            "meta": conv["meta"],
        }
        if with_trace:
            view["turns"] = self.repo.turns(conversation_id)
            view["evidence"] = conv["evidence"]
        return view

    def conversation_list(self, *, status: str | None = None, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        out = []
        for conv in self.repo.list_conversations(status=status, source=source, limit=limit):
            messages = self.repo.messages(conv["id"])
            first = next((m["text"] for m in messages if m["role"] == "customer"), "")
            out.append(
                {
                    "id": conv["id"],
                    "customer_name": conv["customer_name"],
                    "status": conv["status"],
                    "source": conv["source"],
                    "channel": conv["channel"],
                    "created_at": conv["created_at"],
                    "updated_at": conv["updated_at"],
                    "turn_count": conv["turn_count"],
                    "first_message": first[:80],
                    "resolution": (conv["resolution"] or {}).get("status"),
                    "handoff": _handoff_brief(self.repo.latest_handoff_for(conv["id"])),
                    "title": (conv["meta"] or {}).get("title"),
                }
            )
        return out

    def handoff_view(self, handoff_id: str) -> dict[str, Any]:
        handoff = self.repo.get_handoff(handoff_id)
        if handoff is None:
            raise ServiceError(404, "handoff_not_found", "인계 건을 찾을 수 없습니다.")
        conv = self._require(handoff["conversation_id"])
        return {
            **handoff,
            "reason_label": HANDOFF_REASON_LABEL.get(handoff["reason"], handoff["reason"]),
            "conversation": self.conversation_view(conv["id"], with_trace=True),
        }

    def handoff_list(self, status: str | None = None) -> list[dict[str, Any]]:
        out = []
        for handoff in self.repo.list_handoffs(status):
            conv = self.repo.get_conversation(handoff["conversation_id"])
            if conv is None:
                continue
            out.append(
                {
                    "id": handoff["id"],
                    "conversation_id": handoff["conversation_id"],
                    "status": handoff["status"],
                    "reason": handoff["reason"],
                    "reason_label": HANDOFF_REASON_LABEL.get(handoff["reason"], handoff["reason"]),
                    "source": handoff["source"],
                    "created_at": handoff["created_at"],
                    "assigned_to": handoff["assigned_to"],
                    "customer_name": conv["customer_name"],
                    "customer_question": (handoff["snapshot"] or {}).get("customer_question", ""),
                    "conversation_source": conv["source"],
                }
            )
        return out

    # ---- 저장된 상담 기록 ----------------------------------------------------------

    def export_conversation(self, conversation_id: str) -> dict[str, Any]:
        conv = self._require(conversation_id)
        return {
            "conversation": conv,
            "messages": self.repo.messages(conversation_id),
            "turns": self.repo.turns(conversation_id),
            "handoffs": [h for h in self.repo.list_handoffs() if h["conversation_id"] == conversation_id],
        }

    def import_recording(self, data: dict[str, Any], *, recording_id: str, title: str, provenance: dict[str, Any]) -> str:
        """실제 실행에서 내보낸 대화를 '저장된 기록'으로 싣는다. 이미 있으면 건너뛴다."""
        conv = data["conversation"]
        cid = f"rec_{recording_id}"
        if self.repo.get_conversation(cid) is not None:
            return cid
        meta = {**(conv.get("meta") or {}), "title": title, "provenance": provenance, "recorded_source": conv["source"]}
        self.repo.create_conversation(
            customer_id=conv["customer_id"], customer_name=conv["customer_name"], channel=conv["channel"],
            sandbox_id="recorded", source="recorded", meta=meta, conversation_id=cid, created_at=conv["created_at"],
        )  # fmt: skip
        for m in data["messages"]:
            self.repo.add_message(cid, m["role"], m["text"], m["payload"], created_at=m["created_at"])
        for t in data["turns"]:
            record = {k: v for k, v in t.items() if k not in ("id", "conversation_id", "turn_seq")}
            self.repo.add_turn(cid, t["turn_seq"], record)
        for h in data["handoffs"]:
            created = self.repo.create_handoff(
                conversation_id=cid, turn_seq=h["turn_seq"], reason=h["reason"], source=h["source"], summary=h["summary"],
                snapshot=h["snapshot"] or {}, handoff_id=f"{h['id']}_{recording_id}", created_at=h["created_at"],
            )  # fmt: skip
            self.repo.update_handoff(
                created["id"], status=h["status"], assigned_to=h["assigned_to"], assigned_at=h["assigned_at"],
                resolved_at=h["resolved_at"], resolution_note=h["resolution_note"],
            )  # fmt: skip
        self.repo.update_conversation(
            cid, status=conv["status"], turn_count=conv["turn_count"], evidence=conv["evidence"],
            resolution=conv["resolution"], feedback=conv["feedback"],
        )  # fmt: skip
        self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (conv["updated_at"], cid))
        return cid

    def load_recordings(self, directory: Path) -> int:
        count = 0
        for path in sorted(directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.import_recording(data["export"], recording_id=path.stem, title=data["title"], provenance=data["provenance"])
            count += 1
        return count


def _handoff_brief(handoff: dict[str, Any] | None) -> dict[str, Any] | None:
    if handoff is None:
        return None
    return {
        "id": handoff["id"],
        "status": handoff["status"],
        "reason": handoff["reason"],
        "reason_label": HANDOFF_REASON_LABEL.get(handoff["reason"], handoff["reason"]),
        "source": handoff["source"],
        "summary": handoff["summary"],
        "created_at": handoff["created_at"],
        "assigned_to": handoff["assigned_to"],
        "resolved_at": handoff["resolved_at"],
    }
