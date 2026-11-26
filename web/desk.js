// 상담원 화면: 인계 목록(가로 레일) → 선택한 건의 요약·대화·근거·도구 결과 → 답변·종료.

import { $, ago, api, clear, dateTime, el, evidenceValidity, exampleBadge, keyValueView, messageView, mountChrome, sourceBadge, toast, toolStatusPill, traceView } from "/static/common.js";

const FILTERS = [
  { key: "waiting", label: "대기" },
  { key: "assigned", label: "진행" },
  { key: "resolved", label: "종료" },
  { key: "all", label: "전체" },
];
const SOURCE_LABEL = { model: "AI가 판단해 넘김", system: "시스템이 자동으로 넘김", customer: "고객이 직접 요청" };
const QUICK = [
  "안녕하세요, 상담원입니다. 넘겨받은 내용 확인했습니다. 잠시만 기다려 주세요.",
  "확인해 보니 예외로 처리해 드릴 수 있습니다. 바로 접수하겠습니다.",
  "죄송합니다. 정책상 이번 건은 도와드리기 어렵습니다.",
  "사진을 확인했습니다. 환불 금액을 정해 다시 안내드리겠습니다.",
];

const state = { handoffs: [], filter: "waiting", selectedId: null, detail: null, detailKey: "", draft: "", note: "", busy: false, engineOff: false };

function agentName() {
  return $("#agent-name").value.trim() || "데모 상담원";
}

async function init() {
  const release = await mountChrome("desk");
  state.engineOff = !release || !release.engine || release.engine.engine === "off";
  const nameInput = $("#agent-name");
  nameInput.value = localStorage.getItem("bd.agent") || "데모 상담원";
  nameInput.addEventListener("change", () => localStorage.setItem("bd.agent", agentName()));
  state.selectedId = new URLSearchParams(location.search).get("handoff");
  await refresh(true);
  setInterval(() => refresh(false), 3000);
}

async function refresh(first) {
  try {
    state.handoffs = (await api("/v1/handoffs")).handoffs;
  } catch {
    if (first) clear($("#detail")).append(el("div", { class: "empty", text: "인계 목록을 불러오지 못했습니다. 잠시 뒤 다시 시도해 주세요." }));
    return;
  }
  if (first) {
    const selected = state.handoffs.find((h) => h.id === state.selectedId);
    if (selected) state.filter = selected.status;
    else if (!state.handoffs.some((h) => h.status === "waiting")) state.filter = state.handoffs.some((h) => h.status === "assigned") ? "assigned" : "all";
  }
  renderTabs();
  renderRail();
  if (!state.selectedId) {
    const visible = visibleHandoffs();
    if (visible.length) await select(visible[0].id);
    else renderEmpty();
  } else {
    await loadDetail(false);
  }
}

function visibleHandoffs() {
  return state.handoffs.filter((h) => state.filter === "all" || h.status === state.filter);
}

function renderTabs() {
  const tabs = clear($("#tabs"));
  for (const filter of FILTERS) {
    const count = filter.key === "all" ? state.handoffs.length : state.handoffs.filter((h) => h.status === filter.key).length;
    tabs.append(
      el(
        "button",
        { class: `tab ${state.filter === filter.key ? "on" : ""}`, type: "button", role: "tab", dataset: { filter: filter.key }, "aria-selected": state.filter === filter.key ? "true" : "false", onclick: () => setFilter(filter.key) },
        filter.label,
        el("span", { class: "cnt", text: String(count) }),
      ),
    );
  }
}

async function setFilter(key) {
  state.filter = key;
  renderTabs();
  renderRail();
  const visible = visibleHandoffs();
  if (!visible.some((h) => h.id === state.selectedId)) {
    if (visible.length) await select(visible[0].id);
    else {
      state.selectedId = null;
      state.detail = null;
      renderEmpty();
    }
  }
}

function statusPill(status) {
  const map = { waiting: ["대기", "pill-bad"], assigned: ["진행 중", "pill-warn"], resolved: ["종료", "pill-ink"] };
  const [label, cls] = map[status] || [status, "pill"];
  return el("span", { class: `pill ${cls}`, text: label });
}

function renderRail() {
  const rail = clear($("#rail"));
  const visible = visibleHandoffs();
  if (visible.length === 0) {
    rail.append(el("div", { class: "empty", style: { flex: "1" }, text: state.handoffs.length ? "이 상태의 인계 건이 없습니다. 다른 탭을 확인해 보세요." : "아직 인계된 대화가 없습니다." }));
    return;
  }
  for (const handoff of visible) {
    rail.append(
      el(
        "button",
        { class: `ticket ${handoff.id === state.selectedId ? "on" : ""}`, type: "button", dataset: { handoff: handoff.id }, onclick: () => select(handoff.id) },
        el("div", { class: "t-top" }, statusPill(handoff.status), el("span", { class: "pill", text: handoff.reason_label }), handoff.conversation_source === "recorded" ? el("span", { class: "pill pill-ink", text: "저장된 기록" }) : null),
        el("div", { class: "t-q", text: handoff.customer_question || "(고객 메시지 없음)" }),
        el("div", { class: "t-meta", text: `${handoff.customer_name} · ${handoff.status === "waiting" ? `${ago(handoff.created_at)}부터 대기` : ago(handoff.created_at)}${handoff.assigned_to ? ` · ${handoff.assigned_to}` : ""}` }),
      ),
    );
  }
}

function renderEmpty() {
  state.detailKey = "";
  clear($("#detail")).append(
    el(
      "div",
      { class: "card box", style: { marginTop: "14px" } },
      el("h3", { text: "인계 건을 만들어 보려면" }),
      el(
        "ol",
        { style: { margin: "0", paddingLeft: "20px" } },
        el("li", {}, el("a", { href: "/chat", target: "_blank", rel: "noopener", text: "고객 화면" }), "을 새 탭으로 엽니다."),
        el("li", { text: "‘상담원 연결’을 누르거나, ‘상담원이 필요한 문의’ 예시(상한 감귤 환불, 기간이 지난 반품)를 보냅니다." }),
        el("li", { text: "주문 시스템 장애를 ‘오류’로 바꾸고 주문 상태를 물어도 인계됩니다." }),
        el("li", { text: "이 화면에 인계 건이 나타나면 맡아서 답해 보세요. 3초마다 새로 확인합니다." }),
      ),
      state.engineOff ? el("p", { class: "small muted", style: { marginTop: "10px" }, text: "지금 이 서버는 모델 호출이 꺼져 있어 새 대화를 시작할 수 없습니다. 저장된 상담 기록에 인계 건이 있으면 위 목록에 ‘저장된 기록’ 표시와 함께 나타납니다." }) : null,
    ),
  );
}

async function select(handoffId) {
  state.selectedId = handoffId;
  state.draft = "";
  state.note = "";
  state.detailKey = "";
  renderRail();
  await loadDetail(true);
}

async function loadDetail(force) {
  if (!state.selectedId) return;
  let detail;
  try {
    detail = await api(`/v1/handoffs/${state.selectedId}`);
  } catch (error) {
    if (force) clear($("#detail")).append(el("div", { class: "empty", text: error.message }));
    return;
  }
  // 바뀐 것이 없으면 다시 그리지 않는다(입력 중인 답변과 스크롤 위치를 지키기 위해).
  const key = `${detail.id}|${detail.status}|${detail.assigned_to}|${detail.conversation.messages.length}|${detail.conversation.status}`;
  if (!force && key === state.detailKey) return;
  const replyBox = $("#reply-text");
  if (replyBox) state.draft = replyBox.value;
  const noteBox = $("#resolve-note");
  if (noteBox) state.note = noteBox.value;
  const hadFocus = document.activeElement && document.activeElement.id;
  state.detail = detail;
  state.detailKey = key;
  renderDetail();
  if (hadFocus && $(`#${hadFocus}`)) $(`#${hadFocus}`).focus();
}

function renderDetail() {
  const d = state.detail;
  const conv = d.conversation;
  const snapshot = d.snapshot || {};
  const recorded = conv.source === "recorded";
  const cited = new Set(snapshot.cited || []);

  const summary = el(
    "section",
    { class: "card box summary-card", id: "summary-card" },
    el("h3", {}, "AI 인계 요약", statusPill(d.status), el("span", { class: "pill pill-ink", text: d.reason_label }), el("span", { class: "pill", text: SOURCE_LABEL[d.source] || d.source })),
    el("div", { class: "sum-text", text: d.summary || "(요약 없음)" }),
    el("p", { class: "q" }, "넘어올 때의 고객 메시지: ", el("b", { text: snapshot.customer_question || "—" })),
    el(
      "div",
      { class: "msg-actions", style: { marginTop: "10px" } },
      sourceBadge(conv.source),
      el("span", { class: "small muted", text: `${conv.customer_name} (${conv.customer_id}) · ${dateTime(d.created_at)} 인계${d.assigned_to ? ` · 담당 ${d.assigned_to}` : ""}` }),
    ),
    (snapshot.grounding_problems || []).length
      ? el("div", { class: "draft", style: { marginTop: "10px" } }, el("b", { text: "근거 검사에서 걸린 내용" }), el("ul", {}, snapshot.grounding_problems.map((p) => el("li", { text: p }))))
      : null,
  );

  const timeline = el("div", { class: "timeline", id: "timeline" }, conv.messages.map((m) => messageView(m, { handoff: null, deskLink: false, compactSystem: true })));
  const conversationBox = el("section", { class: "panel box" }, el("h3", {}, "대화 이력", el("span", { class: "pill", text: `메시지 ${conv.messages.length}건` })), timeline);

  let actions;
  if (recorded) {
    actions = el("section", { class: "panel box" }, el("h3", { text: "응대" }), el("div", { class: "empty", text: "저장된 상담 기록이라 읽기만 할 수 있습니다." }));
  } else if (d.status === "resolved") {
    actions = el(
      "section",
      { class: "panel box" },
      el("h3", { text: "응대" }),
      el("div", { class: "empty" }, `${dateTime(d.resolved_at)}에 종료된 건입니다.`, d.resolution_note ? ` 처리 메모: ${d.resolution_note}` : "", conv.status === "open" ? " 대화는 AI에게 돌려주었습니다." : " 대화는 닫혔습니다."),
    );
  } else {
    actions = el(
      "section",
      { class: "card box reply-box", id: "reply-box" },
      el("h3", {}, "고객에게 답하기", d.status === "waiting" ? el("button", { class: "btn btn-s btn-butter", type: "button", id: "btn-claim", onclick: claim, text: "이 건 맡기" }) : el("span", { class: "pill pill-warn", text: `${d.assigned_to} 담당` })),
      el("div", { class: "quick" }, QUICK.map((text) => el("button", { class: "chip", type: "button", onclick: () => useQuick(text), text: text.length > 26 ? `${text.slice(0, 26)}…` : text, title: text }))),
      el("textarea", { id: "reply-text", placeholder: "고객에게 보낼 답변을 입력하세요", "aria-label": "답변" }),
      el("div", { class: "reply-actions" }, el("button", { class: "btn btn-primary", type: "button", id: "btn-reply", onclick: reply, text: "답변 보내기" }), el("span", { class: "small muted", text: "대기 중인 건에 답하면 자동으로 맡은 것으로 처리됩니다." })),
      el(
        "div",
        { class: "resolve-row" },
        el("input", { id: "resolve-note", placeholder: "처리 메모(선택)", "aria-label": "처리 메모", maxlength: "200" }),
        el("button", { class: "btn btn-s", type: "button", id: "btn-close", onclick: () => resolve(false), text: "대화 닫고 종료" }),
        el("button", { class: "btn btn-s", type: "button", id: "btn-return", onclick: () => resolve(true), text: "AI에게 돌려주고 종료" }),
      ),
    );
  }

  const evidence = snapshot.evidence || [];
  const evidenceBox = el(
    "section",
    { class: "panel box", id: "evidence-box" },
    el("h3", {}, "근거 목록", el("span", { class: "pill", text: `${evidence.length}건` }), cited.size ? el("span", { class: "pill pill-warn", text: `답변에 인용 ${cited.size}건` }) : null),
    evidence.length === 0
      ? el("div", { class: "empty", text: "이 대화에서 확보한 근거가 없습니다." })
      : el(
          "div",
          { class: "list-gap" },
          evidence.map((ev) => {
            const isTool = ev.kind === "tool";
            let parsed = null;
            if (isTool) {
              try {
                parsed = JSON.parse(ev.text);
              } catch {
                parsed = null;
              }
            }
            const item = el(
              "div",
              { class: `ev-item ${cited.has(ev.id) ? "cited" : ""}` },
              el("div", { class: "ev-title" }, el("span", { class: "pill pill-ink", text: ev.id }), isTool ? exampleBadge("예시 주문 시스템") : null, cited.has(ev.id) ? el("span", { class: "pill pill-warn", text: "인용됨" }) : null, el("span", { text: isTool ? `${ev.meta.tool} 결과` : `${ev.meta.doc_title} › ${ev.meta.heading}` })),
              isTool ? null : evidenceValidity(ev),
            );
            if (isTool && parsed) item.append(el("details", {}, el("summary", { class: "linkbtn", style: { display: "inline-flex", alignItems: "center" }, text: "조회 결과 펼치기" }), el("div", { class: "kv-nested", style: { marginTop: "6px" } }, keyValueView(parsed))));
            else item.append(el("div", { class: "ev-text", text: ev.text, title: "눌러서 전체 보기", onclick: () => item.classList.toggle("open") }));
            return item;
          }),
        ),
  );

  const tools = snapshot.tool_calls || [];
  const toolBox = el(
    "section",
    { class: "panel box", id: "tool-box" },
    el("h3", {}, "인계 직전의 도구 호출", el("span", { class: "pill", text: `${tools.length}건` })),
    tools.length === 0
      ? el("div", { class: "empty", text: "인계된 턴에서는 주문 시스템을 부르지 않았습니다." })
      : el("div", { class: "table-wrap" }, el("table", { class: "data" }, el("thead", {}, el("tr", {}, el("th", { text: "도구" }), el("th", { text: "인자" }), el("th", { text: "결과" }), el("th", { class: "r", text: "시도" }))), el("tbody", {}, tools.map((t) => el("tr", {}, el("td", { class: "mono", text: t.name }), el("td", { class: "mono", text: Object.entries(t.args || {}).map(([k, v]) => `${k}=${v}`).join(", ") || "—" }), el("td", {}, toolStatusPill(t.status)), el("td", { class: "r", text: `${t.attempts}회` })))))),
  );

  const drafts = snapshot.blocked_drafts || [];
  const draftBox = el(
    "section",
    { class: "panel box", id: "draft-box" },
    el("h3", {}, "고객에게 보내지 않은 AI 초안", el("span", { class: `pill ${drafts.length ? "pill-bad" : ""}`, text: `${drafts.length}건` })),
    drafts.length === 0
      ? el("div", { class: "empty", text: "근거 검사에 걸려 보류된 초안이 없습니다." })
      : el("div", { class: "list-gap" }, drafts.map((draft) => el("div", { class: "draft" }, el("div", { class: "d-text", text: draft.message }), (draft.problems || []).length ? el("ul", {}, draft.problems.map((p) => el("li", { text: p }))) : null))),
  );

  const turn = (conv.turns || []).find((t) => t.turn_seq === d.turn_seq);
  const traceBox = el("section", { class: "panel box" }, el("details", {}, el("summary", { class: "linkbtn", style: { display: "inline-flex", alignItems: "center" }, text: "인계된 턴의 실행 기록 펼치기" }), turn ? el("div", { style: { marginTop: "10px" } }, traceView(turn)) : el("div", { class: "empty", style: { marginTop: "10px" }, text: "고객이 버튼으로 요청한 인계라 AI 실행 기록이 없습니다." })));

  clear($("#detail")).append(el("div", { class: "desk-detail" }, el("div", { class: "stack" }, summary, conversationBox, actions), el("div", { class: "stack" }, evidenceBox, toolBox, draftBox, traceBox)));

  const replyBox = $("#reply-text");
  if (replyBox) replyBox.value = state.draft;
  const noteBox = $("#resolve-note");
  if (noteBox) noteBox.value = state.note;
  timeline.scrollTop = timeline.scrollHeight;
}

function useQuick(text) {
  const box = $("#reply-text");
  box.value = text;
  box.focus();
}

async function act(fn) {
  if (state.busy) return;
  state.busy = true;
  try {
    await fn();
    state.detailKey = "";
    state.handoffs = (await api("/v1/handoffs")).handoffs;
    const current = state.handoffs.find((h) => h.id === state.selectedId);
    if (current && state.filter !== "all") state.filter = current.status;
    renderTabs();
    renderRail();
    await loadDetail(true);
  } catch (error) {
    toast(error.message);
  } finally {
    state.busy = false;
  }
}

function claim() {
  return act(() => api(`/v1/handoffs/${state.selectedId}/claim`, { method: "POST", body: { agent_name: agentName() } }));
}

function reply() {
  const text = $("#reply-text").value.trim();
  if (!text) {
    toast("답변을 입력해 주세요.");
    return null;
  }
  return act(async () => {
    await api(`/v1/handoffs/${state.selectedId}/reply`, { method: "POST", body: { agent_name: agentName(), text } });
    state.draft = "";
    $("#reply-text").value = "";
  });
}

function resolve(returnToAi) {
  const note = $("#resolve-note").value;
  return act(async () => {
    await api(`/v1/handoffs/${state.selectedId}/resolve`, { method: "POST", body: { agent_name: agentName(), note, return_to_ai: returnToAi } });
    state.note = "";
    toast(returnToAi ? "종료하고 대화를 AI에게 돌려주었습니다." : "대화를 닫고 종료했습니다.");
  });
}

init();
