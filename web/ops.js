// 운영 화면: 개요(지표) / 검토 대기 / 프롬프트·평가 / 로그.

import { $, api, clear, dateTime, el, int, mountChrome, ms, pct, toast, usd } from "/static/common.js";

const TABS = [
  { key: "overview", label: "개요" },
  { key: "review", label: "검토 대기" },
  { key: "prompts", label: "프롬프트·평가" },
  { key: "logs", label: "로그" },
];
const STAGE_LABEL = { retrieval: "지식 검색", llm_agent: "에이전트 모델", llm_checks: "판정 모델(근거·해결)", tools: "주문 시스템 호출" };
const STAGE_COLOR = { retrieval: "butter", llm_agent: "", llm_checks: "lilac", tools: "coral" };
const TOOL_STATUS_LABEL = { ok: "성공", not_found: "찾지 못함", timeout: "시간 초과", unavailable: "시스템 오류", rejected: "거부", invalid: "잘못된 인자", denied: "실행 거부" };
const OUTCOME_LABEL = { answered: "답변", asked: "되묻기", handoff: "인계" };
const KIND_LABEL = { agent: "에이전트", grounding: "근거 판정", resolution: "해결 판정" };
const EVENT_TYPES = ["", "turn.completed", "llm.call", "tool.call", "kb.search", "grounding.blocked", "kb.issue", "handoff.created", "handoff.assigned", "handoff.agent_reply", "handoff.resolved", "feedback", "error", "conversation.created", "demo.fault_set"];

const state = { tab: "overview", source: "", liveSource: "live", metrics: null, release: null, logType: "", autoLogs: false, logTimer: null };

async function init() {
  state.release = await mountChrome("ops");
  const engine = state.release && state.release.engine ? state.release.engine.engine : "off";
  state.liveSource = engine === "off" ? "live" : engine;
  const wanted = new URLSearchParams(location.search).get("tab");
  if (TABS.some((tab) => tab.key === wanted)) state.tab = wanted;
  renderTabs();
  renderFilters();
  $("#btn-refresh").addEventListener("click", () => load(true));
  await load(false);
  setInterval(() => {
    if (state.tab === "overview" || state.tab === "review") load(false);
  }, 10000);
}

function renderTabs() {
  const tabs = clear($("#tabs"));
  const flagged = state.metrics ? state.metrics.flagged.length : 0;
  for (const tab of TABS) {
    tabs.append(
      el(
        "button",
        { class: `tab ${state.tab === tab.key ? "on" : ""}`, type: "button", role: "tab", dataset: { tab: tab.key }, "aria-selected": state.tab === tab.key ? "true" : "false", onclick: () => setTab(tab.key) },
        tab.label,
        tab.key === "review" && flagged ? el("span", { class: "cnt", text: String(flagged) }) : null,
      ),
    );
  }
  for (const tab of TABS) $(`#tab-${tab.key}`).classList.toggle("hidden", tab.key !== state.tab);
  $("#filters").classList.toggle("hidden", !(state.tab === "overview" || state.tab === "review"));
}

async function setTab(key) {
  state.tab = key;
  renderTabs();
  clearInterval(state.logTimer);
  state.logTimer = null;
  await load(false);
}

function renderFilters() {
  const live = state.liveSource;
  const chips = [
    { value: "", label: "전체" },
    { value: live, label: live === "live" ? "실시간 (실제 모델 호출)" : "실시간 (규칙 엔진, LLM 아님)" },
    { value: "recorded", label: "저장된 상담 기록" },
  ];
  const box = clear($("#filters"));
  box.append(el("span", { class: "small muted", text: "집계 대상" }));
  for (const chip of chips) {
    box.append(el("button", { class: `chip ${state.source === chip.value ? "on" : ""}`, type: "button", dataset: { source: chip.value || "all" }, onclick: () => setSource(chip.value), text: chip.label }));
  }
}

async function setSource(value) {
  state.source = value;
  renderFilters();
  await load(false);
}

async function load(announce) {
  try {
    if (state.tab === "overview" || state.tab === "review") {
      state.metrics = await api(`/v1/metrics/summary${state.source ? `?source=${encodeURIComponent(state.source)}` : ""}`);
      renderTabs();
      if (state.tab === "overview") renderOverview();
      else renderReview();
    } else if (state.tab === "prompts") {
      await renderPrompts();
    } else {
      await renderLogs();
    }
    if (announce) toast("새로 불러왔습니다.");
  } catch (error) {
    toast(error.message);
  }
}

// ---- 개요 --------------------------------------------------------------------------

function kpi(cls, label, value, unit, sub, def, extra) {
  return el(
    "div",
    { class: `card kpi ${cls}` },
    el("div", { class: "k-label", text: label }),
    el("div", { class: "k-value" }, value, unit ? el("small", { text: unit }) : null),
    el("div", { class: "k-sub", text: sub }),
    extra || null,
    el("div", { class: "k-def", text: def }),
  );
}

function barRow(label, value, max, text, color) {
  const width = max > 0 ? Math.max(2, (value / max) * 100) : 0;
  return el("div", { class: "bar-row" }, el("span", { text: label }), el("div", { class: "bar-track" }, el("span", { class: `bar-fill ${color || ""}`, style: { width: `${width}%` } })), el("span", { class: "val", text }));
}

function statTable(rows) {
  return el("div", { class: "table-wrap" }, el("table", { class: "data kv" }, el("tbody", {}, rows.map(([label, value]) => el("tr", {}, el("td", { text: label }), el("td", { class: "r", text: value }))))));
}

function renderOverview() {
  const m = state.metrics;
  const box = clear($("#tab-overview"));
  const r = m.resolution;
  const t = m.turns;
  const noData = m.conversations.ai_involved === 0;

  const resolvedCount = r.resolved_confirmed + r.resolved_assumed;
  const grid = el(
    "div",
    { class: "kpi-grid" },
    kpi(
      "kpi-a",
      "해결률",
      r.rate === null ? "—" : pct(r.rate).replace("%", ""),
      r.rate === null ? null : "%",
      r.rate === null ? "집계할 대화 없음" : `해결 ${int(resolvedCount)}건 ÷ 집계 대상 ${int(r.denominator)}건`,
      "(확인된 해결 + 가정된 해결) ÷ (해결 + 미해결 + 상담원 인계). 인사뿐인 대화, 고객의 답을 기다리는 대화, 판정하지 못한 대화는 분모에서 뺍니다.",
      el(
        "div",
        { class: "mini-stats" },
        el("span", { class: "pill pill-ok", text: `확인된 해결 ${r.resolved_confirmed}` }),
        el("span", { class: "pill", text: `가정된 해결 ${r.resolved_assumed}` }),
        el("span", { class: "pill pill-warn", text: `미해결 ${r.unresolved}` }),
        el("span", { class: "pill pill-bad", text: `상담원 인계 ${r.handed_off}` }),
        el("span", { class: "pill pill-ink", text: `분모 제외 ${r.excluded}` }),
      ),
    ),
    kpi(
      "kpi-b",
      "인계율",
      m.handoff.rate === null ? "—" : pct(m.handoff.rate).replace("%", ""),
      m.handoff.rate === null ? null : "%",
      m.handoff.rate === null ? "집계할 대화 없음" : `인계 ${int(m.handoff.conversations)}건 ÷ AI 관여 ${int(m.conversations.ai_involved)}건 · 지금 대기 ${int(m.handoff.waiting)}건`,
      "상담원에게 넘어간 대화 ÷ 고객이 AI에게 한 번 이상 말한 대화.",
    ),
    kpi(
      "kpi-c",
      "근거 보류",
      int(m.grounding.blocked_drafts),
      "건",
      `다시 쓴 턴 ${int(m.grounding.turns_with_revision)} · 끝내 맞추지 못해 인계 ${int(m.grounding.ungrounded_handoffs)}`,
      "AI 초안이 근거 검사에 걸려 고객에게 나가지 않은 횟수. 걸린 초안은 한 번 다시 쓰게 하고, 그래도 맞지 않으면 상담원에게 넘깁니다.",
    ),
    kpi(
      "kpi-d",
      "응답 시간 (중앙값)",
      t.latency_ms.p50 === null ? "—" : ms(t.latency_ms.p50).replace(/(ms|초)$/, ""),
      t.latency_ms.p50 === null ? null : ms(t.latency_ms.p50).match(/(ms|초)$/)[0],
      t.latency_ms.p50 === null ? "집계할 응답 없음" : `p95 ${ms(t.latency_ms.p95)} · 최대 ${ms(t.latency_ms.max)} · 응답 ${int(t.total)}건`,
      "고객 메시지를 받은 때부터 AI 응답을 저장할 때까지, 서버 안에서 잰 시간입니다(네트워크 왕복 제외).",
    ),
  );
  box.append(grid);
  if (noData) {
    box.append(el("div", { class: "empty", style: { marginTop: "18px" } }, "이 조건에 해당하는 대화가 아직 없습니다. ", el("a", { href: "/chat", text: "고객 화면" }), "에서 문의를 보내면 여기에 집계됩니다."));
  }

  const stageMax = Math.max(1, ...Object.values(t.stage_latency_ms).map((s) => s.p95 || 0));
  const stageBox = el(
    "section",
    { class: "panel box" },
    el("h3", { text: "어디에서 시간이 걸리나" }),
    el(
      "div",
      { class: "bars" },
      Object.entries(t.stage_latency_ms).map(([key, s]) => barRow(STAGE_LABEL[key] || key, s.p50 || 0, stageMax, s.n ? `${ms(s.p50)} / ${ms(s.p95)}` : "—", STAGE_COLOR[key])),
    ),
    el("p", { class: "small muted", style: { marginTop: "10px" }, text: "값은 턴마다 그 단계에 쓴 시간의 중앙값 / p95 입니다. 막대는 중앙값." }),
  );

  const llm = m.llm;
  const realCalls = llm.by_engine.anthropic || 0;
  const llmBox = el(
    "section",
    { class: "panel box" },
    el("h3", {}, "모델 호출", el("span", { class: `pill ${realCalls ? "pill-ok" : "pill-warn"}`, text: realCalls ? "실제 모델 호출 포함" : "실제 모델 호출 없음" })),
    statTable([
      ["호출 수", int(llm.calls)],
      ["입력 토큰", int(llm.input_tokens)],
      ["출력 토큰", int(llm.output_tokens)],
      ["캐시에서 읽은 토큰", int(llm.cache_read_tokens)],
      ["추정 비용", llm.cost_usd === null ? "—" : usd(llm.cost_usd)],
    ]),
    el("p", { class: "small muted", style: { marginTop: "10px" }, text: "비용은 응답의 토큰 수에 공개 단가를 곱한 추정치이며 청구서가 아닙니다. 규칙 엔진 응답은 토큰·비용이 0으로 기록됩니다." }),
    el(
      "div",
      { class: "mini-stats", style: { marginTop: "8px" } },
      Object.entries(llm.by_engine).map(([k, v]) => el("span", { class: `pill ${k === "anthropic" ? "pill-ok" : ""}`, text: `${k === "anthropic" ? "실제 모델" : "규칙 엔진(LLM 아님)"} ${v}턴` })),
      // 규칙 엔진만 돈 서버에서는 모델 이름을 보여 주지 않는다(그 이름의 모델이 답한 것이 아니므로).
      realCalls ? Object.entries(llm.by_model).map(([k, v]) => el("span", { class: "pill", text: `${k} ${v}턴` })) : null,
      Object.entries(llm.by_prompt).map(([k, v]) => el("span", { class: "pill pill-warn", text: `프롬프트 ${k} ${v}턴` })),
    ),
  );

  const tools = m.tools;
  const toolMax = Math.max(1, ...Object.values(tools.by_name));
  const toolBox = el(
    "section",
    { class: "panel box" },
    el("h3", {}, "도구 호출", el("span", { class: `pill ${tools.failures ? "pill-bad" : "pill-ok"}`, text: `실패 ${int(tools.failures)}건` })),
    tools.calls === 0 ? el("div", { class: "empty", text: "도구 호출 기록이 없습니다." }) : el("div", { class: "bars" }, Object.entries(tools.by_name).sort((a, b) => b[1] - a[1]).map(([name, count]) => barRow(name, count, toolMax, `${int(count)}회`, "butter"))),
    el("div", { class: "mini-stats", style: { marginTop: "10px" } }, Object.entries(tools.by_status).map(([status, count]) => el("span", { class: `pill ${status === "ok" ? "pill-ok" : status === "not_found" ? "pill-warn" : "pill-bad"}`, text: `${TOOL_STATUS_LABEL[status] || status} ${count}` }))),
    el("p", { class: "small muted", style: { marginTop: "10px" }, text: "실패 = 시간 초과 + 시스템 오류(재시도까지 실패). ‘찾지 못함’은 실패가 아니라 정상 응답입니다." }),
  );

  const reasons = m.handoff.by_reason;
  const reasonMax = Math.max(1, ...reasons.map((x) => x.count));
  const sourceLabel = { model: "AI 판단", system: "시스템 자동", customer: "고객 요청" };
  const reasonBox = el(
    "section",
    { class: "panel box" },
    el("h3", { text: "왜 상담원에게 넘어갔나" }),
    reasons.length === 0 ? el("div", { class: "empty", text: "인계된 대화가 없습니다." }) : el("div", { class: "bars" }, reasons.map((x) => barRow(x.label, x.count, reasonMax, `${int(x.count)}건`, "coral"))),
    el("div", { class: "mini-stats", style: { marginTop: "10px" } }, Object.entries(m.handoff.by_source).map(([k, v]) => el("span", { class: "pill", text: `${sourceLabel[k] || k} ${v}` }))),
  );

  const c = m.conversations;
  const convBox = el(
    "section",
    { class: "panel box" },
    el("h3", { text: "대화와 응답" }),
    statTable([
      ["대화 수 (AI 관여)", `${int(c.total)} (${int(c.ai_involved)})`],
      ["진행 중 / 상담원 응대 중 / 종료", `${int(c.open)} / ${int(c.handed_off)} / ${int(c.closed)}`],
      ["AI 응답", Object.entries(t.by_outcome).map(([k, v]) => `${OUTCOME_LABEL[k] || k} ${v}`).join(" · ") || "—"],
      ["처리 오류가 난 턴", int(t.errors)],
      ["검색 결과가 0건이었던 턴", int(m.retrieval.zero_hit_turns)],
      ["답변에 인용된 근거 (문서 / 주문 조회)", `${int(m.retrieval.cited_kb)} / ${int(m.retrieval.cited_tool)}`],
    ]),
  );

  const budget = m.engine.budget;
  const budgetBox = el(
    "section",
    { class: "panel box" },
    el("h3", { text: "공개 데모 보호" }),
    statTable([
      ["지금 엔진", m.engine.label],
      ["하루 모델 비용 상한(추정)", usd(budget.daily_usd_cap)],
      ["오늘 쓴 금액(추정)", usd(budget.spent_usd_today)],
      ["오늘 모델 호출 수", int(budget.llm_calls_today)],
    ]),
    el("p", { class: "small muted", style: { marginTop: "10px" }, text: budget.exhausted ? "오늘 상한에 닿아 새 문의를 받지 않습니다." : "상한에 닿으면 새 문의를 받지 않고 저장된 상담 기록만 보여 줍니다." }),
  );

  box.append(el("div", { class: "ops-grid" }, stageBox, reasonBox, llmBox, toolBox, convBox, budgetBox));
}

// ---- 검토 대기 -----------------------------------------------------------------------

function renderReview() {
  const box = clear($("#tab-review"));
  const flagged = state.metrics.flagged;
  box.append(el("p", { class: "muted", style: { marginTop: "16px" }, text: "근거 검사에 걸린 초안, 처리 오류, 주문 시스템 호출 실패, 지식 문서 점검 요청이 있었던 턴입니다. 최근 50건까지 보여 줍니다." }));
  if (flagged.length === 0) {
    box.append(el("div", { class: "empty", style: { marginTop: "12px" }, text: "검토할 턴이 없습니다." }));
    return;
  }
  box.append(
    el(
      "div",
      { class: "list-gap", style: { marginTop: "12px" }, id: "flag-list" },
      flagged.map((flag) =>
        el(
          "div",
          { class: "panel flag" },
          el(
            "div",
            { class: "flag-top" },
            flag.reasons.map((reason) => el("span", { class: "pill pill-bad", text: reason })),
            el("span", { class: "pill", text: `응답: ${OUTCOME_LABEL[flag.outcome] || flag.outcome}` }),
            el("span", { class: "small muted", text: `${dateTime(flag.started_at)} · ${flag.turn_seq}번째 문의` }),
          ),
          flag.blocked.map((draft) => el("div", { class: "draft" }, el("b", { text: "고객에게 보내지 않은 초안" }), el("div", { class: "d-text", text: draft.message }), (draft.problems || []).length ? el("ul", {}, draft.problems.map((p) => el("li", { text: p }))) : null)),
          flag.kb_issue ? el("div", { class: "ev-item cited" }, el("b", { text: "지식 문서 점검 요청: " }), flag.kb_issue) : null,
          el("div", {}, el("a", { class: "btn btn-s", href: `/chat?conversation=${encodeURIComponent(flag.conversation_id)}`, target: "_blank", rel: "noopener", text: "대화와 실행 과정 열기" }), " ", el("span", { class: "mono muted", text: flag.conversation_id })),
        ),
      ),
    ),
  );
}

// ---- 프롬프트·평가 ----------------------------------------------------------------------

async function renderPrompts() {
  const box = clear($("#tab-prompts"));
  const data = await api("/v1/prompts");
  const section = el("section", { class: "ops-section" }, el("h2", { text: "프롬프트 버전" }), el("p", { class: "muted", text: "프롬프트는 버전 파일로 관리합니다. 평가에 쓰인 버전은 잠겨서 내용을 바꿀 수 없고, 바꾸려면 새 버전을 만듭니다." }));
  const list = el("div", { class: "prompt-list", id: "prompt-list" });
  for (const version of data.versions) {
    const active = data.active[version.kind] === version.id;
    const viewer = el("div", { class: "hidden viewer" });
    const toggle = el("button", {
      class: "btn btn-s",
      type: "button",
      dataset: { prompt: version.id },
      text: "본문 보기",
      onclick: async () => {
        if (!viewer.classList.contains("hidden")) {
          viewer.classList.add("hidden");
          toggle.textContent = "본문 보기";
          return;
        }
        if (!viewer.firstChild) {
          try {
            const full = await api(`/v1/prompts/${encodeURIComponent(version.id)}`);
            viewer.append(el("pre", { class: "code", text: full.system }));
            const overrides = Object.entries(full.tool_descriptions || {});
            if (overrides.length) viewer.append(el("p", { class: "small muted", style: { margin: "8px 0 4px" }, text: "이 버전이 바꾼 도구 설명" }), el("pre", { class: "code", text: overrides.map(([k, v]) => `${k}: ${v}`).join("\n\n") }));
          } catch (error) {
            viewer.append(el("div", { class: "empty", text: error.message }));
          }
        }
        viewer.classList.remove("hidden");
        toggle.textContent = "본문 접기";
      },
    });
    list.append(
      el(
        "div",
        { class: "panel prompt-item" },
        el(
          "div",
          { class: "p-top" },
          el("b", { class: "mono", text: version.id }),
          el("span", { class: "pill", text: KIND_LABEL[version.kind] || version.kind }),
          active ? el("span", { class: "pill pill-ok", text: "사용 중" }) : null,
          version.locked ? el("span", { class: "pill pill-ink", text: "잠김" }) : el("span", { class: "pill pill-warn", text: "잠기지 않음" }),
          el("span", { class: "small muted mono", text: `sha256 ${version.sha256.slice(0, 12)} · ${version.created} · ${int(version.system_chars)}자` }),
        ),
        el("div", { text: version.summary }),
        version.changes ? el("div", { class: "small muted" }, el("b", { text: "이전 버전에서 바꾼 점: " }), version.changes) : null,
        el("div", {}, toggle),
        viewer,
      ),
    );
  }
  section.append(list);
  box.append(section);

  const evalSection = el("section", { class: "ops-section", id: "eval-section" }, el("h2", { text: "평가 결과" }));
  let evals = null;
  try {
    evals = await api("/v1/evals");
  } catch {
    evals = { available: false, note: "이 서버에는 평가 결과가 실려 있지 않습니다. 저장소의 평가 문서(docs/)에서 문항 수, 판정 기준, 반복 실행 결과를 확인할 수 있습니다.", sections: [] };
  }
  if (evals.note) evalSection.append(el("p", { class: "muted", text: evals.note }));
  if (!evals.available) {
    if (!evals.note) evalSection.append(el("div", { class: "empty", text: "평가 결과가 없습니다." }));
  } else {
    for (const section of evals.sections || []) evalSection.append(evalTable(section));
  }
  box.append(evalSection);
  markScrollable(evalSection);
}

function formatCell(value, format) {
  if (value === null || value === undefined || value === "") return "—";
  if (format === "pct") return pct(value);
  if (format === "int") return int(value);
  if (format === "ms") return ms(value);
  if (format === "usd") return usd(value);
  if (format === "ci") {
    if (Array.isArray(value) && value.length === 2) return `${pct(value[0])} ~ ${pct(value[1])}`;
    return String(value);
  }
  return String(value);
}

function evalTable(section) {
  const columns = section.columns || [];
  const numeric = (format) => ["pct", "int", "ms", "usd", "ci"].includes(format);
  return el(
    "div",
    { class: "eval-block" },
    el("h3", { text: section.title }),
    section.description ? el("p", { class: "small muted", text: section.description }) : null,
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        { class: "data wide" },
        el("thead", {}, el("tr", {}, columns.map((col) => el("th", { class: numeric(col.format) ? "r" : null, text: col.label })))),
        el("tbody", {}, (section.rows || []).map((row) => el("tr", {}, columns.map((col) => el("td", { class: numeric(col.format) ? "r" : null, text: formatCell(row[col.key], col.format) }))))),
      ),
    ),
    el("p", { class: "scroll-hint hidden", text: "표가 넓어 옆으로 밀면 나머지 열이 보입니다. 맨 왼쪽 열(실행 이름)은 고정됩니다." }),
    section.footnote ? el("p", { class: "small muted", style: { marginTop: "6px" }, text: section.footnote }) : null,
  );
}

// 표가 보이는 폭보다 넓으면 오른쪽 끝을 어둡게 하고 안내 글을 보인다(밀어서 더 볼 수 있다는 표시).
function markScrollable(root) {
  for (const wrap of root.querySelectorAll(".table-wrap")) {
    const update = () => {
      const more = wrap.scrollWidth - wrap.clientWidth - wrap.scrollLeft > 4;
      if (more) wrap.setAttribute("data-more", "");
      else wrap.removeAttribute("data-more");
      const hint = wrap.parentElement.querySelector(".scroll-hint");
      if (hint) hint.classList.toggle("hidden", wrap.scrollWidth <= wrap.clientWidth + 4);
    };
    wrap.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();
  }
}

// ---- 로그 --------------------------------------------------------------------------

async function renderLogs() {
  const box = $("#tab-logs");
  if (!box.firstChild) {
    box.append(
      el(
        "div",
        { class: "filters" },
        el("label", { class: "small muted", for: "log-type", text: "이벤트 종류" }),
        el(
          "select",
          {
            class: "sel",
            id: "log-type",
            onchange: (event) => {
              state.logType = event.target.value;
              loadLogs();
            },
          },
          EVENT_TYPES.map((type) => el("option", { value: type, text: type || "전체" })),
        ),
        el(
          "label",
          { class: "toggle" },
          el("input", {
            type: "checkbox",
            id: "log-auto",
            onchange: (event) => {
              clearInterval(state.logTimer);
              state.logTimer = event.target.checked ? setInterval(loadLogs, 4000) : null;
            },
          }),
          "4초마다 새로 불러오기",
        ),
      ),
      el("p", { class: "small muted", style: { margin: "10px 0" }, text: "같은 이벤트가 서버의 표준 출력에 한 줄 JSON으로도 남습니다. 고객 메시지 본문은 이벤트에 싣지 않습니다." }),
      el("div", { class: "panel", id: "log-list" }),
    );
  }
  await loadLogs();
}

function hms(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return [date.getHours(), date.getMinutes(), date.getSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

async function loadLogs() {
  const list = $("#log-list");
  if (!list) return;
  try {
    const { events } = await api(`/v1/events?limit=120${state.logType ? `&type=${encodeURIComponent(state.logType)}` : ""}`);
    clear(list);
    if (events.length === 0) {
      list.append(el("div", { class: "empty", style: { border: "0" }, text: "기록된 이벤트가 없습니다." }));
      return;
    }
    for (const event of events) {
      const data = { ...(event.data || {}) };
      list.append(
        el(
          "div",
          { class: "log-row" },
          el("span", { class: "num muted", text: hms(event.ts) }),
          el("span", { class: "l-type", text: event.type }),
          el("span", { class: "l-data", text: `${event.conversation_id ? `${event.conversation_id} ` : ""}${JSON.stringify(data)}` }),
        ),
      );
    }
  } catch (error) {
    clear(list).append(el("div", { class: "empty", style: { border: "0" }, text: error.message }));
  }
}

init();
