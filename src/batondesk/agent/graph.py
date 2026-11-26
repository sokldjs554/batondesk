"""에이전트 한 턴의 흐름 (LangGraph 상태 그래프).

    prepare ─▶ model ─┬─▶ tools ─▶ model …        (도구 호출이 있으면 반복)
                      ├─▶ nudge ─▶ model           (응답 제출 도구 없이 끝내면 다시 요청)
                      ├─▶ verify ─┬─▶ END          (근거와 맞음)
                      │           ├─▶ revise ─▶ model   (어긋남: 한 번 다시 쓰게 함)
                      │           └─▶ fallback ─▶ END   (다시 써도 어긋남: 상담원에게)
                      └─▶ fallback ─▶ END          (모델 오류·거절·반복 한도)

그래프로 쓴 이유: 분기(도구 반복, 재작성, 인계)가 코드 흐름에 묻히지 않고 선언으로 남고,
노드가 끝날 때마다 진행 상황을 화면에 흘려보낼 수 있다(astream).

고객에게 나가는 글은 반드시 verify 를 지난다. 모델이 쓴 초안이 근거와 어긋나면 고객에게
보이지 않고, 보류 기록(blocked)으로만 남는다.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from batondesk.agent.evidence import Evidence, EvidencePool
from batondesk.agent.protocol import (
    MODEL_HANDOFF_REASONS,
    RESPOND_TOOL,
    AgentOutput,
    context_block,
    customer_block,
    respond_tool_definition,
)
from batondesk.checks.grounding import GroundingChecker, GroundingResult
from batondesk.llm.base import LLMClient, LLMError, LLMRequest, LLMResponse
from batondesk.prompts.registry import PromptVersion
from batondesk.tools.executor import PendingAction, ToolContext, ToolExecutor
from batondesk.tools.specs import tool_definitions

ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]

_FOLLOWUP_HINT = re.compile(r"^(그럼|그러면|그건|그거|그게|이건|이거|거기|아까|그리고|근데|그런데|혹시 그|그 ?경우)")
MAX_CARRIED_KB = 6
MAX_CARRIED_TOOL = 6
MAX_NUDGES = 2

FALLBACK_MESSAGES = {
    "ungrounded_answer": "죄송합니다. 정확한 안내를 위해 확인이 더 필요한 내용이라 상담원에게 연결해 드리겠습니다. 지금까지의 대화 내용은 함께 전달합니다.",
    "agent_error": "죄송합니다. 지금 답변을 준비하는 중에 문제가 생겼습니다. 상담원에게 연결해 드리겠습니다. 지금까지의 대화 내용은 함께 전달합니다.",
}


@dataclass
class HistoryItem:
    role: Literal["customer", "ai", "agent"]
    text: str


@dataclass
class TurnInput:
    conversation_id: str
    turn_seq: int
    customer_id: str
    customer_name: str
    sandbox_id: str
    now: datetime
    history: list[HistoryItem]
    customer_text: str
    pool: EvidencePool
    pending: dict[str, PendingAction] = field(default_factory=dict)
    # 평가 전용: 모델의 첫 초안을 이 글로 바꿔치기해, 근거 검사가 어긋난 답을 실제로 막는지 본다.
    inject_draft: str | None = None


@dataclass
class TurnConfig:
    model: str
    prompt: PromptVersion
    effort: str = "low"
    max_tokens: int = 4000
    retrieval_k: int = 5
    max_tool_rounds: int = 6
    grounding_mode: Literal["block", "flag", "off"] = "block"
    max_revisions: int = 1


@dataclass
class TurnResult:
    output: AgentOutput
    handoff_source: Literal["model", "system"] | None
    grounding: GroundingResult | None
    steps: list[dict[str, Any]]
    blocked: list[dict[str, Any]]
    fresh_evidence_ids: list[str]
    error: str | None
    revisions: int
    engine: str

    @property
    def outcome(self) -> str:
        return {"answer": "answered", "ask": "asked", "handoff": "handoff"}[self.output.action]


class AgentState(TypedDict, total=False):
    turn: TurnInput
    ctx: ToolContext
    messages: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    rounds: int
    response: LLMResponse | None
    draft: AgentOutput | None
    grounding: GroundingResult | None
    revisions: int
    nudges: int
    respond_call_id: str | None
    route: str
    blocked: list[dict[str, Any]]
    final: AgentOutput | None
    handoff_source: str | None
    error: str | None
    fail_reason: str | None
    fresh_ids: list[str]
    injected: bool
    engine: str


def _history_messages(history: list[HistoryItem]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in history:
        if item.role == "customer":
            messages.append({"role": "user", "content": customer_block(item.text)})
        elif item.role == "ai":
            messages.append({"role": "assistant", "content": item.text})
        else:
            messages.append({"role": "user", "content": f"<상담원_메시지>\n{item.text}\n</상담원_메시지>"})
    return messages


def retrieval_queries(history: list[HistoryItem], text: str) -> list[str]:
    """첫 검색에 쓸 질의들.

    짧거나 지시어로 시작하는 메시지는 앞 대화에 기대는 후속 질문일 수 있어, 직전 고객 메시지를
    붙인 질의를 하나 더 만든다. 원래 메시지만으로 한 검색도 그대로 둔다 — 고객이 화제를 바꾼
    경우 앞 메시지가 검색을 엉뚱한 곳으로 끌고 가기 때문이다.
    """
    previous = next((h.text for h in reversed(history) if h.role == "customer"), None)
    if previous and (len(text) < 20 or _FOLLOWUP_HINT.match(text.strip())):
        return [text, f"{previous} {text}"]
    return [text]


def conversation_for_check(history: list[HistoryItem], customer_text: str) -> list[dict[str, str]]:
    label = {"customer": "고객", "ai": "AI", "agent": "상담원"}
    out = [{"role": label[h.role], "text": h.text} for h in history]
    out.append({"role": "고객", "text": customer_text})
    return out


def build_agent_graph(llm: LLMClient, executor: ToolExecutor, checker: GroundingChecker, config: TurnConfig) -> Any:
    descriptions = config.prompt.tool_descriptions
    tools = [*tool_definitions(descriptions), respond_tool_definition(descriptions.get(RESPOND_TOOL))]

    async def prepare(state: AgentState) -> dict[str, Any]:
        turn = state["turn"]
        ctx = ToolContext(
            customer_id=turn.customer_id,
            sandbox_id=turn.sandbox_id,
            turn_seq=turn.turn_seq,
            today=turn.now.date(),
            pool=turn.pool,
            pending=turn.pending,
            retrieval_k=config.retrieval_k,
        )
        carried_all = turn.pool.all()
        carried = [e for e in carried_all if e.kind == "tool"][-MAX_CARRIED_TOOL:]
        carried += [e for e in carried_all if e.kind == "kb" and e.meta.get("cited")][-MAX_CARRIED_KB:]
        queries = retrieval_queries(turn.history, turn.customer_text)
        started = time.perf_counter()
        per_query = [executor.search(q, ctx) for q in queries]
        retrieval_ms = round((time.perf_counter() - started) * 1000, 1)
        # 질의가 둘이면 순위를 번갈아 섞어, 어느 한쪽의 상위 결과가 밀려나지 않게 한다.
        fresh: list[Evidence] = []
        for rank in range(config.retrieval_k):
            for found in per_query:
                if rank < len(found) and found[rank].id not in {e.id for e in fresh}:
                    fresh.append(found[rank])
        fresh = fresh[: config.retrieval_k + (2 if len(queries) > 1 else 0)]
        query = " / ".join(queries)
        carried_ids = {e.id for e in carried}
        fresh_new = [e for e in fresh if e.id not in carried_ids]
        messages = _history_messages(turn.history)
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": customer_block(turn.customer_text)},
                    {
                        "type": "text",
                        "text": context_block(
                            now=turn.now,
                            customer_name=turn.customer_name,
                            customer_id=turn.customer_id,
                            query=query,
                            fresh=fresh_new,
                            carried=carried,
                        ),
                    },
                ],
            }
        )
        step = {
            "type": "retrieval",
            "query": query,
            "latency_ms": retrieval_ms,
            "hits": [{"evidence_id": e.id, "chunk_id": e.meta["chunk_id"], "title": e.title, "score": e.meta["score"]} for e in fresh],
        }
        return {
            "ctx": ctx,
            "messages": messages,
            "steps": [step],
            "rounds": 0,
            "revisions": 0,
            "nudges": 0,
            "blocked": [],
            "fresh_ids": [e.id for e in fresh],
            "injected": False,
            "engine": llm.engine,
        }

    async def model(state: AgentState) -> dict[str, Any]:
        request = LLMRequest(
            purpose="agent",
            model=config.model,
            system=config.prompt.system,
            messages=state["messages"],
            tools=tools,
            max_tokens=config.max_tokens,
            effort=config.effort,
        )
        steps = list(state["steps"])
        try:
            response = await llm.complete(request)
        except LLMError as exc:
            steps.append({"type": "llm_error", "purpose": "agent", "kind": exc.kind, "attempts": exc.attempts, "error": str(exc)[:300]})
            return {"steps": steps, "response": None, "error": f"llm_{exc.kind}", "fail_reason": "agent_error", "route": "fallback"}
        steps.append(response.trace("agent"))
        update: dict[str, Any] = {"steps": steps, "response": response, "engine": response.engine, "respond_call_id": None}
        if response.stop_reason == "refusal":
            return {**update, "error": f"refusal:{response.refusal_category}", "fail_reason": "agent_error", "route": "fallback"}
        if response.stop_reason == "max_tokens":
            return {**update, "error": "truncated", "fail_reason": "agent_error", "route": "fallback"}

        respond_calls = [c for c in response.tool_calls if c.name == RESPOND_TOOL]
        other_calls = [c for c in response.tool_calls if c.name != RESPOND_TOOL]
        if not response.tool_calls:
            # 응답 제출 도구 없이 글만 쓰고 끝냈다. 그 글은 검사를 거치지 않았으므로 고객에게 내보내지 않는다.
            if state["nudges"] >= MAX_NUDGES:
                return {**update, "error": "no_final_response", "fail_reason": "agent_error", "route": "fallback"}
            return {**update, "route": "nudge"}
        if other_calls or len(respond_calls) > 1:
            # 조회 도구와 응답 제출을 한 번에 불렀다면 조회만 실행하고, 결과를 본 뒤 다시 제출하게 한다.
            return {**update, "route": "tools" if state["rounds"] < config.max_tool_rounds else "limit"}

        call = respond_calls[0]
        try:
            draft = AgentOutput.model_validate(call.input)
            if not draft.message.strip():
                raise ValueError("message 가 비어 있습니다")
        except (ValueError, ValidationError) as exc:
            steps.append({"type": "respond_rejected", "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
            if state["rounds"] >= config.max_tool_rounds:
                return {**update, "steps": steps, "error": "bad_output", "fail_reason": "agent_error", "route": "fallback"}
            return {**update, "steps": steps, "route": "tools"}
        if draft.action != "handoff":
            draft.handoff_reason, draft.handoff_summary = "none", ""
        elif draft.handoff_reason not in MODEL_HANDOFF_REASONS or draft.handoff_reason == "none":
            draft.handoff_reason = "needs_human_review"
        return {**update, "draft": draft, "respond_call_id": call.id, "route": "verify"}

    def after_model(state: AgentState) -> str:
        return state["route"]

    async def nudge(state: AgentState) -> dict[str, Any]:
        response = state["response"]
        assert response is not None
        messages = list(state["messages"])
        messages.append({"role": "assistant", "content": response.raw_content})
        messages.append(
            {
                "role": "user",
                "content": (
                    "<시스템_안내>\n방금 쓴 글은 고객에게 전달되지 않았습니다. 고객에게 보낼 응답은 respond 도구로 제출해야 합니다. "
                    "확인이 더 필요하면 먼저 필요한 도구를 부르고, 준비가 되면 respond 를 부르세요.\n</시스템_안내>"
                ),
            }
        )
        steps = [*state["steps"], {"type": "nudge", "reason": "응답 제출 도구 없이 턴을 끝냄"}]
        return {"messages": messages, "steps": steps, "nudges": state["nudges"] + 1}

    async def limit(state: AgentState) -> dict[str, Any]:
        return {"error": "tool_round_limit", "fail_reason": "agent_error"}

    async def run_tools(state: AgentState) -> dict[str, Any]:
        response = state["response"]
        assert response is not None
        ctx = state["ctx"]
        action_calls = [c for c in response.tool_calls if c.name != RESPOND_TOOL]
        results = await asyncio.gather(*(executor.run(c.name, c.input, ctx) for c in action_calls))
        by_id = {call.id: result for call, result in zip(action_calls, results, strict=True)}
        steps = list(state["steps"])
        steps.extend(r.trace() for r in results)
        rejected = next((s["error"] for s in reversed(steps) if s["type"] == "respond_rejected"), None) if not action_calls else None
        # 한 번의 응답에 든 도구 호출의 결과는 한 user 메시지에 모두 담는다.
        content: list[dict[str, Any]] = []
        for call in response.tool_calls:
            if call.id in by_id:
                result = by_id[call.id]
                content.append({"type": "tool_result", "tool_use_id": call.id, "content": result.content, "is_error": result.is_error})
            elif rejected:
                message = (
                    f"응답이 형식에 맞지 않아 제출되지 않았습니다({rejected}). 고객에게 보낼 message 를 채워 respond 를 다시 부르세요."
                )
                content.append({"type": "tool_result", "tool_use_id": call.id, "content": message, "is_error": True})
            else:
                message = "아직 제출되지 않았습니다. 함께 부른 도구의 결과를 확인한 뒤 respond 를 한 번만 다시 부르세요."
                content.append({"type": "tool_result", "tool_use_id": call.id, "content": message, "is_error": True})
        messages = list(state["messages"])
        messages.append({"role": "assistant", "content": response.raw_content})
        messages.append({"role": "user", "content": content})
        return {"steps": steps, "messages": messages, "rounds": state["rounds"] + 1}

    async def verify(state: AgentState) -> dict[str, Any]:
        turn = state["turn"]
        draft = state["draft"]
        assert draft is not None
        steps = list(state["steps"])
        injected = state.get("injected", False)
        if turn.inject_draft and not injected and draft.action == "answer":
            steps.append({"type": "eval_injection", "original": draft.message, "injected": turn.inject_draft})
            draft = draft.model_copy(update={"message": turn.inject_draft})
            injected = True

        unknown = [c for c in draft.citations if c not in turn.pool]
        if unknown:
            draft = draft.model_copy(update={"citations": [c for c in draft.citations if c in turn.pool]})
            steps.append({"type": "citation_fix", "removed": unknown})

        if config.grounding_mode == "off":
            return {"steps": steps, "final": draft, "grounding": None, "injected": injected}

        result, check_steps = await checker.check(
            draft.message,
            turn.pool.all(),
            conversation_for_check(turn.history, turn.customer_text),
            use_llm=True if draft.action == "answer" else "auto",
            today=turn.now,
        )
        steps.extend(check_steps)
        steps.append(
            {
                "type": "grounding",
                "verdict": result.verdict,
                "mode": result.mode,
                "contradicted": result.contradicted,
                "unsupported": result.unsupported,
                "problems": result.problems,
                "fact_flags": result.fact_flags,
            }
        )
        base = {"steps": steps, "grounding": result, "draft": draft, "injected": injected}
        if result.grounded or config.grounding_mode == "flag":
            return {**base, "final": draft}
        blocked = [*state["blocked"], {"message": draft.message, "action": draft.action, "problems": result.problems}]
        return {**base, "final": None, "blocked": blocked}

    def after_verify(state: AgentState) -> str:
        if state.get("final") is not None:
            return "done"
        return "revise" if state["revisions"] < config.max_revisions else "give_up"

    async def revise(state: AgentState) -> dict[str, Any]:
        response = state["response"]
        result = state["grounding"]
        call_id = state.get("respond_call_id")
        assert response is not None and result is not None and call_id is not None
        feedback = "\n".join(f"- {p}" for p in result.problems) or "- 근거로 확인되지 않는 내용이 있습니다."
        messages = list(state["messages"])
        messages.append({"role": "assistant", "content": response.raw_content})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "is_error": True,
                        "content": (
                            "제출한 응답은 근거 검사를 통과하지 못해 고객에게 보내지 않았습니다.\n"
                            f"{feedback}\n"
                            "근거에 있는 내용만으로 다시 작성해 respond 로 제출하세요. 필요한 근거가 없으면 도구로 확인하고, "
                            "그래도 답할 수 없으면 action 을 handoff 로 하세요."
                        ),
                    }
                ],
            }
        )
        steps = [*state["steps"], {"type": "revise", "problems": result.problems}]
        return {"messages": messages, "steps": steps, "revisions": state["revisions"] + 1, "draft": None}

    async def give_up(state: AgentState) -> dict[str, Any]:
        return {"fail_reason": "ungrounded_answer", "error": "ungrounded_after_revision"}

    async def fallback(state: AgentState) -> dict[str, Any]:
        """모델 없이 만드는 안전한 인계. 무엇이 있었는지는 실행 기록에서 모아 요약한다."""
        turn = state["turn"]
        reason = state.get("fail_reason") or "agent_error"
        tool_steps = [s for s in state["steps"] if s.get("type") == "tool"]
        failed = [s for s in tool_steps if s.get("status") in ("timeout", "unavailable")]
        lines = [f"고객 메시지: {turn.customer_text}"]
        if tool_steps:
            lines.append("실행한 도구: " + ", ".join(f"{s['name']}({s['status']})" for s in tool_steps))
        if reason == "ungrounded_answer":
            problems = [p for b in state.get("blocked", []) for p in b.get("problems", [])][:4]
            lines.append("AI 초안이 근거 검사에서 걸려 고객에게 보내지 않았습니다.")
            lines.extend(f"- {p}" for p in problems)
            lines.append("권하는 조치: 아래 근거 목록을 확인하고 정확한 내용을 직접 안내해 주세요.")
        else:
            lines.append(f"AI 처리 오류: {state.get('error')}")
            if failed:
                lines.append("주문 시스템 호출 실패가 있었습니다.")
            lines.append("권하는 조치: 고객 문의를 처음부터 확인해 주세요.")
        final = AgentOutput(
            action="handoff",
            message=FALLBACK_MESSAGES[reason],
            citations=[],
            handoff_reason=reason,
            handoff_summary="\n".join(lines),
        )
        steps = [*state["steps"], {"type": "fallback", "reason": reason, "error": state.get("error")}]
        return {"final": final, "handoff_source": "system", "steps": steps}

    builder = StateGraph(AgentState)
    builder.add_node("prepare", prepare)
    builder.add_node("model", model)
    builder.add_node("tools", run_tools)
    builder.add_node("nudge", nudge)
    builder.add_node("limit", limit)
    builder.add_node("verify", verify)
    builder.add_node("revise", revise)
    builder.add_node("give_up", give_up)
    builder.add_node("fallback", fallback)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "model")
    builder.add_conditional_edges(
        "model", after_model, {"tools": "tools", "nudge": "nudge", "limit": "limit", "verify": "verify", "fallback": "fallback"}
    )
    builder.add_edge("nudge", "model")
    builder.add_edge("tools", "model")
    builder.add_edge("limit", "fallback")
    builder.add_conditional_edges("verify", after_verify, {"done": END, "revise": "revise", "give_up": "give_up"})
    builder.add_edge("revise", "model")
    builder.add_edge("give_up", "fallback")
    builder.add_edge("fallback", END)
    return builder.compile()


PROGRESS_LABEL = {
    "prepare": "지식 문서를 검색했습니다",
    "tools": "주문 시스템을 확인했습니다",
    "verify": "답변을 근거와 대조했습니다",
    "revise": "근거와 맞지 않아 다시 작성합니다",
    "nudge": "응답을 다시 정리합니다",
    "fallback": "상담원 연결을 준비합니다",
}


async def run_turn(graph: Any, turn: TurnInput, on_progress: ProgressFn | None = None) -> TurnResult:
    state: dict[str, Any] = {"turn": turn}
    recursion_limit = 60
    async for update in graph.astream({"turn": turn}, {"recursion_limit": recursion_limit}, stream_mode="updates"):
        for node, delta in update.items():
            if isinstance(delta, dict):
                state.update(delta)
            if on_progress is None:
                continue
            if node == "model":
                response = state.get("response")
                names = ", ".join(c.name for c in response.tool_calls if c.name != RESPOND_TOOL) if response is not None else ""
                if names:
                    await on_progress({"stage": "tool_call", "label": f"확인 중: {names}"})
            elif node in PROGRESS_LABEL:
                await on_progress({"stage": node, "label": PROGRESS_LABEL[node]})

    final: AgentOutput | None = state.get("final")
    assert final is not None, "그래프가 최종 출력 없이 끝났습니다"
    handoff_source = state.get("handoff_source")
    if final.action == "handoff" and handoff_source is None:
        handoff_source = "model"
    for evidence_id in final.citations:
        ev: Evidence | None = turn.pool.get(evidence_id)
        if ev is not None and ev.kind == "kb":
            ev.meta["cited"] = True
    return TurnResult(
        output=final,
        handoff_source=handoff_source,
        grounding=state.get("grounding"),
        steps=state.get("steps", []),
        blocked=state.get("blocked", []),
        fresh_evidence_ids=state.get("fresh_ids", []),
        error=state.get("error"),
        revisions=state.get("revisions", 0),
        engine=state.get("engine", "unknown"),
    )
