// 고객 화면: 채팅, 출처 보기, 상담원 연결, 해결 여부 피드백, 데모 도구(예시 고객·장애 재현·실행 과정·저장된 기록).

import { $, ApiError, ago, api, clear, el, exampleBadge, messageView, mountChrome, ms, put, sourceBadge, toast, traceView, won } from "/static/common.js";

const MAX_CHARS = 600;
const params = new URLSearchParams(location.search);

const state = {
  engine: "off",
  customers: [],
  customerId: null,
  convId: null,
  conv: null,
  lastSeq: 0,
  rendered: new Set(),
  turns: new Map(),
  sending: false,
  pollTimer: null,
  readOnly: false,
  embed: params.get("embed") === "1",
};

const log = $("#chat-log");
const input = $("#input");

const SCENARIOS = [
  {
    group: "문서에 답이 있는 문의",
    items: [
      { t: "무료배송으로 받은 상품을 단순변심으로 반품하면 배송비가 얼마인가요?" },
      { t: "카드로 결제한 주문을 취소하면 환불은 언제 들어오나요?" },
      { t: "도서산간 지역은 배송비가 얼마나 더 붙나요?" },
    ],
  },
  {
    group: "이어지는 질문",
    hint: "위에서부터 차례로 보내면 앞의 대화를 이어받는지 볼 수 있습니다.",
    items: [
      { c: "C-1001", t: "BM-24011 주문 지금 어떤 상태예요?" },
      { c: "C-1001", t: "그럼 그거 단순변심으로 반품하면 얼마 돌려받아요?" },
      { c: "C-1001", t: "신청은 언제까지 해야 돼요?" },
    ],
  },
  {
    group: "문서에 답이 없는 문의",
    items: [{ t: "해외로도 배송해 주나요?" }, { t: "매장에 직접 가서 받을 수 있나요?" }, { t: "산 뒤에 가격이 내리면 차액을 돌려주나요?" }],
  },
  {
    group: "문서끼리 다른 내용",
    hint: "오래된 FAQ, 끝난 공지, 서로 다른 금액이 적힌 문서가 일부러 섞여 있습니다.",
    items: [
      { t: "무료배송은 얼마 이상 사야 되나요? 3만원이라고 본 것 같은데요." },
      { t: "반품은 14일까지 된다고 들었는데 맞나요?" },
      { c: "C-1001", t: "BM-24014 사이드테이블 반품하면 회수비가 얼마예요?" },
    ],
  },
  {
    group: "없는 주문번호",
    items: [
      { t: "BM-99999 주문 배송 조회해 주세요." },
      { c: "C-1001", t: "BM-24021 주문 취소해 주세요.", note: "다른 고객의 주문" },
    ],
  },
  {
    group: "주문 시스템 장애",
    hint: "아래 '장애 재현'을 오류나 시간 초과로 바꾼 뒤 보내 보세요.",
    items: [
      { c: "C-1001", t: "BM-24012 주문 어디쯤 왔나요?" },
      { c: "C-1002", t: "BM-24024 배송이 며칠째 그대로예요. 확인해 주세요." },
    ],
  },
  {
    group: "상담원이 필요한 문의",
    items: [
      { c: "C-1001", t: "BM-24016 감귤이 일부 상해서 왔어요. 얼마나 환불받을 수 있나요?" },
      { c: "C-1003", t: "BM-24031 텀블러, 반품 기간이 하루 지났는데 이번만 받아 주세요." },
      { c: "C-1002", t: "결제가 두 번 된 것 같아요. 확인해 주세요." },
    ],
  },
  {
    group: "취소·반품 실행",
    hint: "AI가 금액을 안내하면 '네, 진행해 주세요'라고 답해 보세요. 고객 확인 전에는 실행되지 않습니다.",
    items: [
      { c: "C-1002", t: "BM-24022 주문 취소해 주세요." },
      { c: "C-1001", t: "BM-24011 가습기 단순변심으로 반품 접수해 주세요." },
      { t: "네, 진행해 주세요." },
    ],
  },
];

const FAULTS = [
  { mode: "none", label: "정상", note: "주문 시스템이 정상으로 응답합니다." },
  { mode: "slow", label: "느림", note: "1.2초 늦게 응답합니다(답은 옵니다)." },
  { mode: "timeout", label: "시간 초과", note: "응답하지 않습니다. 도구가 제한 시간 뒤 포기합니다." },
  { mode: "error", label: "오류", note: "500 오류를 돌려줍니다." },
];

const ERROR_COPY = {
  network_error: ["연결이 끊겼습니다", "메시지가 전달되지 않았을 수 있습니다. 네트워크를 확인한 뒤 다시 보내 주세요.", "retry"],
  llm_unavailable: ["이 서버는 모델 호출이 꺼져 있습니다", "새 문의에는 답할 수 없습니다. '저장된 상담 기록'에서 실제 모델로 실행한 대화를 볼 수 있습니다.", null],
  budget_exhausted: ["오늘의 데모 한도를 다 썼습니다", "하루에 쓸 수 있는 모델 호출 비용 상한에 닿았습니다. '저장된 상담 기록'을 확인해 주세요.", null],
  rate_limited: ["요청이 많습니다", "잠시 뒤에 다시 보내 주세요.", "retry"],
  conversation_closed: ["종료된 대화입니다", "상담원이 상담을 종료했습니다. 새 대화를 시작해 주세요.", "new"],
  conversation_too_long: ["대화가 길어졌습니다", "한 대화에서 보낼 수 있는 메시지 수를 넘었습니다. 새 대화를 시작해 주세요.", "new"],
  conversation_not_found: ["대화를 찾을 수 없습니다", "서버가 다시 시작되어 이전 대화가 지워졌을 수 있습니다. 새 대화를 시작해 주세요.", "new"],
  recorded_conversation: ["저장된 상담 기록입니다", "읽기 전용이라 메시지를 보낼 수 없습니다.", "new"],
  internal_error: ["처리 중 오류가 났습니다", "잠시 뒤에 다시 보내 주세요.", "retry"],
};

// ---- 준비 -------------------------------------------------------------------------

async function init() {
  if (state.embed) document.body.classList.add("embed");
  const release = await mountChrome("chat", { nav: !state.embed });
  state.engine = release && release.engine ? release.engine.engine : "off";

  try {
    state.customers = (await api("/v1/demo/customers")).customers;
  } catch {
    state.customers = [];
  }
  const wanted = params.get("customer") || sessionStorage.getItem("bd.customer");
  state.customerId = (state.customers.find((c) => c.customer_id === wanted) || state.customers[0] || {}).customer_id || null;

  renderCustomers();
  renderScenarios();
  renderFaults();
  loadRecordings();
  bind();

  const readId = params.get("conversation");
  if (readId) {
    await openReadOnly(readId);
    return;
  }
  const saved = state.customerId ? sessionStorage.getItem(`bd.conv.${state.customerId}`) : null;
  if (saved && (await restore(saved))) return;
  fresh();
  // 모델 호출이 꺼진 서버에서 할 수 있는 일은 저장된 기록을 보는 것뿐이라 그 구역을 먼저 연다(?pane=recordings 로도 열 수 있다).
  if (params.get("pane") === "recordings" || state.engine === "off") showPane("recordings");
}

function showPane(name) {
  for (const tab of document.querySelectorAll("#demo-tabs .tab")) {
    const on = tab.dataset.pane === name;
    tab.classList.toggle("on", on);
    tab.setAttribute("aria-selected", on ? "true" : "false");
  }
  for (const pane of ["tools", "trace", "recordings"]) $(`#pane-${pane}`).classList.toggle("hidden", pane !== name);
  if (name === "trace") $("#trace-dot").classList.add("hidden");
  $("#last-turn").classList.toggle("hidden", name === "trace" || !$("#last-turn").firstChild);
}

function currentPane() {
  const tab = document.querySelector("#demo-tabs .tab.on");
  return tab ? tab.dataset.pane : "tools";
}

function bind() {
  for (const tab of document.querySelectorAll("#demo-tabs .tab")) tab.addEventListener("click", () => showPane(tab.dataset.pane));
  $("#composer").addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.value);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send(input.value);
    }
  });
  input.addEventListener("input", onInput);
  $("#btn-new").addEventListener("click", () => startNew());
  $("#btn-handoff").addEventListener("click", requestHandoff);
  $("#btn-reset").addEventListener("click", resetSandbox);
}

function onInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight + 2, 130)}px`;
  const length = input.value.length;
  const counter = $("#composer-count");
  counter.textContent = `${length} / ${MAX_CHARS}`;
  counter.classList.toggle("err", length > MAX_CHARS);
  if (length <= MAX_CHARS) setComposerMessage("");
}

function setComposerMessage(text) {
  const node = $("#composer-msg");
  node.textContent = text;
  node.classList.toggle("err", Boolean(text));
}

function customer() {
  return state.customers.find((c) => c.customer_id === state.customerId) || null;
}

// ---- 대화 상태 ----------------------------------------------------------------------

function resetState() {
  stopPolling();
  state.convId = null;
  state.conv = null;
  state.lastSeq = 0;
  state.rendered = new Set();
  state.turns = new Map();
  state.readOnly = false;
  clear(log);
  clear($("#trace")).append(el("div", { class: "empty", text: "문의를 보내면 검색한 문서, 모델·도구 호출, 근거 검사 결과가 여기에 표시됩니다." }));
  clear($("#last-turn")).classList.add("hidden");
  $("#trace-dot").classList.add("hidden");
}

function fresh() {
  resetState();
  const who = customer();
  log.append(
    el(
      "div",
      { class: "msg ai", id: "greeting" },
      el("div", { class: "who-line" }, el("span", { class: "pill", text: "자동 안내" })),
      el("div", {
        class: "bubble",
        text: `안녕하세요${who ? `, ${who.name} 님` : ""}. 바통마켓 고객센터입니다.\n주문·배송·취소·반품·환불을 물어보세요. 답변 아래의 출처를 누르면 근거를 확인할 수 있고, 제가 해결하기 어려운 일은 상담원에게 넘겨 드립니다.`,
      }),
    ),
  );
  updateChrome();
}

function startNew() {
  if (state.customerId) sessionStorage.removeItem(`bd.conv.${state.customerId}`);
  if (params.get("conversation")) {
    const next = new URLSearchParams(location.search);
    next.delete("conversation");
    history.replaceState(null, "", `${location.pathname}${next.toString() ? `?${next}` : ""}`);
    params.delete("conversation");
  }
  fresh();
  input.focus();
}

async function restore(conversationId) {
  try {
    const view = await api(`/v1/conversations/${conversationId}?trace=true`);
    if (view.source === "recorded" || view.customer_id !== state.customerId) return false;
    resetState();
    adopt(view);
    return true;
  } catch {
    sessionStorage.removeItem(`bd.conv.${state.customerId}`);
    return false;
  }
}

async function openReadOnly(conversationId) {
  resetState();
  state.readOnly = true;
  try {
    const view = await api(`/v1/conversations/${conversationId}?trace=true`);
    adopt(view);
  } catch (error) {
    log.append(errorBubble("대화를 불러오지 못했습니다", error.message, "new"));
    updateChrome();
  }
}

// 서버가 준 대화 모습을 화면 상태로 받아들인다.
function adopt(view) {
  state.convId = view.id;
  state.conv = view;
  for (const turn of view.turns || []) state.turns.set(turn.turn_seq, turn);
  sync(view.messages);
  updateChrome();
  const last = Math.max(0, ...state.turns.keys());
  if (last) showTrace(last, { scroll: false });
  if (state.readOnly) showPane("trace");
  if (view.status === "handed_off" && !state.readOnly) startPolling();
}

async function ensureConversation() {
  if (state.convId) return;
  const view = await api("/v1/conversations", { method: "POST", body: { customer_id: state.customerId, channel: state.embed ? "widget" : "web" } });
  state.convId = view.id;
  state.conv = view;
  sessionStorage.setItem(`bd.conv.${state.customerId}`, view.id);
}

function sync(messages) {
  const greeting = $("#greeting");
  for (const message of messages || []) {
    if (state.rendered.has(message.seq)) continue;
    state.rendered.add(message.seq);
    state.lastSeq = Math.max(state.lastSeq, message.seq);
    const pending = message.role === "customer" ? log.querySelector(".msg.customer.pending") : null;
    if (pending) {
      pending.classList.remove("pending");
      pending.dataset.seq = message.seq;
      continue;
    }
    log.append(
      messageView(message, {
        onTrace: state.embed ? null : (turnSeq) => showTrace(turnSeq, { scroll: true }),
        handoff: state.conv ? state.conv.handoff : null,
        deskLink: !state.embed,
      }),
    );
  }
  if (greeting && state.readOnly) greeting.remove();
  renderFeedback();
  log.scrollTop = log.scrollHeight;
}

function renderFeedback() {
  log.querySelectorAll(".feedback").forEach((node) => node.remove());
  const conv = state.conv;
  if (!conv || state.readOnly) return;
  const last = log.lastElementChild;
  if (!last || !last.classList.contains("ai") || last.id === "greeting") return;
  if (conv.status !== "open") return;
  if (conv.feedback) {
    last.append(el("div", { class: "feedback", text: conv.feedback === "resolved" ? "‘해결됐어요’로 기록했습니다. 감사합니다." : "‘아직이에요’로 기록했습니다." }));
    if (conv.feedback === "not_resolved") {
      last.lastElementChild.append(el("button", { class: "btn btn-s btn-coral", type: "button", onclick: requestHandoff, text: "상담원 연결" }));
    }
    return;
  }
  last.append(
    el(
      "div",
      { class: "feedback" },
      "문의가 해결됐나요?",
      el("button", { class: "btn btn-s", type: "button", id: "fb-yes", onclick: () => sendFeedback(true), text: "해결됐어요" }),
      el("button", { class: "btn btn-s", type: "button", id: "fb-no", onclick: () => sendFeedback(false), text: "아직이에요" }),
    ),
  );
}

async function sendFeedback(resolved) {
  try {
    const view = await api(`/v1/conversations/${state.convId}/feedback`, { method: "POST", body: { resolved } });
    state.conv = { ...state.conv, ...view, messages: undefined };
    renderFeedback();
    log.scrollTop = log.scrollHeight;
  } catch (error) {
    toast(error.message);
  }
}

// ---- 위쪽 표시·배너 -------------------------------------------------------------------

function updateChrome() {
  const conv = state.conv;
  const sub = clear($("#chat-sub"));
  const who = conv ? conv.customer_name : customer() ? customer().name : "";
  if (who) sub.append(el("span", { text: `${who} 님` }));
  if (conv) {
    const provenance = (conv.meta && conv.meta.provenance) || {};
    sub.append(sourceBadge(conv.source));
    const recordedModel = provenance.agent_model || provenance.model;
    if (conv.source === "recorded" && recordedModel) sub.append(el("span", { class: "pill", text: recordedModel }));
    const status = { open: ["AI 응대 중", "pill-ok"], handed_off: ["상담원 응대 중", "pill-bad"], closed: ["종료", "pill-ink"] }[conv.status];
    if (status) sub.append(el("span", { class: `pill ${status[1]}`, text: status[0] }));
  } else {
    sub.append(el("span", { class: "muted", text: "예시 고객으로 문의합니다" }));
  }

  const banner = $("#chat-banner");
  banner.className = "chat-banner hidden";
  clear(banner);
  let canType = true;
  let canHandoff = true;
  if (state.readOnly) {
    banner.className = "chat-banner readonly";
    const provenance = (conv && conv.meta && conv.meta.provenance) || {};
    const parts = [];
    if (conv && conv.source === "recorded") {
      // 이 기록이 어디서 나왔는지: 실제 모델 호출인지, 어떤 모델·프롬프트·커밋인지, 고객·상담원 메시지를 누가 썼는지.
      const model = provenance.agent_model || provenance.model;
      const commit = provenance.git_sha || provenance.commit;
      parts.push(`저장된 상담 기록(읽기 전용)${provenance.kind ? " — " + provenance.kind : ""}`);
      if (model) parts.push(`모델 ${model}`);
      if (provenance.agent_prompt) parts.push(`프롬프트 ${provenance.agent_prompt}`);
      if (provenance.recorded_at) parts.push(`기록 ${String(provenance.recorded_at).slice(0, 10)}`);
      if (commit) parts.push(`커밋 ${String(commit).slice(0, 7)}`);
      if (provenance.customer_messages) parts.push(`고객 메시지: ${provenance.customer_messages}`);
      if (provenance.agent_messages && provenance.agent_messages !== "없음") parts.push(`상담원 메시지: ${provenance.agent_messages}`);
    } else {
      parts.push("운영 화면에서 연 대화입니다(읽기 전용).");
    }
    banner.append(el("span", { text: parts.join(" · ") }));
    if (provenance.run_url) banner.append(" ", el("a", { href: provenance.run_url, target: "_blank", rel: "noopener", text: "실행 로그" }));
    banner.append(" ", el("button", { class: "linkbtn", type: "button", onclick: startNew, text: "새 대화 시작" }));
    canType = false;
    canHandoff = false;
  } else if (conv && conv.status === "closed") {
    banner.className = "chat-banner closed";
    banner.append(el("span", { text: "상담이 종료되었습니다." }), el("button", { class: "btn btn-s btn-primary", type: "button", id: "btn-new-inline", onclick: startNew, text: "새 대화" }));
    canType = false;
    canHandoff = false;
  } else if (conv && conv.status === "handed_off") {
    banner.className = "chat-banner standby";
    banner.textContent = "상담원이 대화를 이어받았습니다. 지금 보내는 메시지는 상담원에게 전달되고, AI는 답하지 않습니다.";
    canHandoff = false;
  } else if (state.engine === "off") {
    banner.className = "chat-banner off";
    banner.textContent = "이 서버는 모델 호출이 꺼져 있어 새 문의에 답하지 않습니다. ‘저장된 상담 기록’에서 실제 모델로 실행한 대화를 볼 수 있습니다.";
    canType = false;
    canHandoff = false;
  }
  // 읽기 전용 보기에서는 입력 영역과 상담원 연결 버튼을 아예 감춘다.
  $("#composer").classList.toggle("hidden", state.readOnly);
  document.querySelector(".composer-meta").classList.toggle("hidden", state.readOnly);
  $("#btn-handoff").classList.toggle("hidden", state.readOnly);
  input.disabled = !canType;
  $("#btn-send").disabled = !canType || state.sending;
  const handoffButton = $("#btn-handoff");
  handoffButton.disabled = !canHandoff || state.sending;
  handoffButton.textContent = conv && conv.status === "handed_off" ? "상담원 연결됨" : "상담원 연결";
  input.placeholder = conv && conv.status === "handed_off" ? "상담원에게 보낼 메시지" : "문의 내용을 입력하세요";
}

function setStatus(text) {
  const node = $("#chat-status");
  node.classList.toggle("hidden", !text);
  $("#chat-status-text").textContent = text || "";
}

function setSending(on) {
  state.sending = on;
  if (!on) setStatus("");
  updateChrome();
}

// ---- 보내기 -------------------------------------------------------------------------

async function send(raw, { retry = false } = {}) {
  if (state.sending || state.readOnly) return;
  const text = raw.trim();
  if (!text) {
    setComposerMessage("문의 내용을 입력해 주세요.");
    return;
  }
  if (text.length > MAX_CHARS) {
    setComposerMessage(`메시지는 ${MAX_CHARS}자까지 보낼 수 있습니다. 지금 ${text.length}자입니다.`);
    return;
  }
  log.querySelectorAll(".error-bubble").forEach((node) => node.parentElement.remove());
  if (!retry || !log.querySelector(".msg.customer.pending")) {
    log.append(el("div", { class: "msg customer pending" }, el("div", { class: "bubble", text })));
  }
  input.value = "";
  onInput();
  log.scrollTop = log.scrollHeight;
  setSending(true);
  setStatus("문의를 접수했습니다");
  try {
    await ensureConversation();
    const result = await streamMessage(state.convId, text, (event) => setStatus(event.label));
    state.conv = result.conversation;
    sync(result.conversation.messages);
    if (result.standby) {
      startPolling();
    } else {
      await loadTurns();
      if (result.message && result.message.payload) showTrace(result.message.payload.turn_seq, { scroll: false });
      if (result.conversation.status === "handed_off") startPolling();
    }
  } catch (error) {
    showSendError(error, text);
  } finally {
    setSending(false);
    if (!input.disabled && !state.embed && window.matchMedia("(min-width: 1081px)").matches) input.focus();
  }
}

async function streamMessage(conversationId, text, onProgress) {
  let response;
  try {
    response = await fetch(`/v1/conversations/${conversationId}/messages/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ text }),
    });
  } catch {
    throw new ApiError(0, "network_error", "서버에 연결하지 못했습니다.");
  }
  if (!response.ok || !response.body) {
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    const err = data && data.error;
    throw new ApiError(response.status, (err && err.code) || `http_${response.status}`, (err && err.message) || "요청을 처리하지 못했습니다.");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut = buffer.indexOf("\n\n");
      while (cut !== -1) {
        const block = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        cut = buffer.indexOf("\n\n");
        let event = "message";
        let data = "";
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        const parsed = JSON.parse(data);
        if (event === "progress") onProgress(parsed);
        else if (event === "error") throw new ApiError(parsed.status || 500, parsed.code, parsed.message);
        else if (event === "result") result = parsed;
      }
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(0, "network_error", "응답을 받는 중에 연결이 끊겼습니다.");
  }
  if (!result) throw new ApiError(0, "network_error", "응답을 끝까지 받지 못했습니다.");
  return result;
}

function errorBubble(title, body, action, text) {
  const box = el("div", { class: "error-bubble", role: "alert" }, el("b", { text: title }), el("div", { text: body }));
  if (action === "retry" && text) box.append(el("button", { class: "btn btn-s", type: "button", id: "btn-retry", onclick: () => send(text, { retry: true }), text: "다시 보내기" }));
  if (action === "new") box.append(el("button", { class: "btn btn-s btn-primary", type: "button", onclick: startNew, text: "새 대화 시작" }));
  return el("div", { class: "msg system" }, box);
}

function showSendError(error, text) {
  const code = error.code || "internal_error";
  if (code === "message_too_long" || code === "empty_message") {
    log.querySelectorAll(".msg.customer.pending").forEach((node) => node.remove());
    input.value = text;
    onInput();
    setComposerMessage(error.message);
    return;
  }
  const [title, body, action] = ERROR_COPY[code] || ["요청을 처리하지 못했습니다", error.message || "잠시 뒤에 다시 시도해 주세요.", "retry"];
  if (code === "llm_unavailable") state.engine = "off";
  if (code === "conversation_closed" && state.conv) state.conv = { ...state.conv, status: "closed" };
  if (action !== "retry") log.querySelectorAll(".msg.customer.pending").forEach((node) => node.classList.remove("pending"));
  log.append(errorBubble(title, body, action, text));
  log.scrollTop = log.scrollHeight;
}

async function requestHandoff() {
  if (state.sending || state.readOnly) return;
  setSending(true);
  setStatus("상담원 연결을 요청하고 있습니다");
  try {
    await ensureConversation();
    const result = await api(`/v1/conversations/${state.convId}/handoff`, { method: "POST" });
    state.conv = result.conversation;
    sync(result.conversation.messages);
    startPolling();
  } catch (error) {
    showSendError(error, "");
  } finally {
    setSending(false);
  }
}

// ---- 인계 뒤: 상담원 메시지 받기 ---------------------------------------------------------

function startPolling() {
  if (state.pollTimer || state.readOnly) return;
  state.pollTimer = setInterval(poll, 2000);
}

function stopPolling() {
  clearInterval(state.pollTimer);
  state.pollTimer = null;
}

async function poll() {
  if (!state.convId || state.sending) return;
  try {
    const view = await api(`/v1/conversations/${state.convId}?after_seq=${state.lastSeq}`);
    state.conv = view;
    sync(view.messages);
    updateChrome();
    if (view.status !== "handed_off") stopPolling();
  } catch (error) {
    if (error.status === 404) stopPolling();
  }
}

// ---- 실행 과정 -----------------------------------------------------------------------

async function loadTurns() {
  try {
    const view = await api(`/v1/conversations/${state.convId}?trace=true&after_seq=${state.lastSeq}`);
    for (const turn of view.turns || []) state.turns.set(turn.turn_seq, turn);
  } catch {
    /* 실행 과정은 부가 정보라 실패해도 대화는 이어 간다 */
  }
}

async function showTrace(turnSeq, { scroll }) {
  if (!state.turns.has(turnSeq)) await loadTurns();
  const turn = state.turns.get(turnSeq);
  const box = clear($("#trace"));
  const strip = clear($("#last-turn"));
  if (!turn) {
    box.append(el("div", { class: "empty", text: "이 응답의 실행 기록을 찾지 못했습니다." }));
    strip.classList.add("hidden");
    return;
  }
  box.append(el("div", { class: "small muted", text: `${turnSeq}번째 문의에 대한 응답` }), traceView(turn));

  // 다른 구역을 보고 있을 때도 방금 응답이 어떻게 만들어졌는지 한 줄로 보여 준다.
  const verdict = turn.grounding_verdict === "grounded" ? ["근거와 맞음", "pill-ok"] : turn.grounding_verdict === "ungrounded" ? ["근거와 어긋남", "pill-bad"] : ["근거 검사 안 함", ""];
  put(
    strip,
    el("span", { class: "small", text: "방금 응답" }),
    el("span", { class: `pill ${verdict[1]}`, text: verdict[0] }),
    turn.blocked_count ? el("span", { class: "pill pill-bad", text: `보류한 초안 ${turn.blocked_count}` }) : null,
    turn.tool_errors ? el("span", { class: "pill pill-bad", text: `도구 실패 ${turn.tool_errors}` }) : null,
    el("span", { class: "small muted num", text: `${ms(turn.latency_ms)} · 모델 ${turn.llm_calls}회 · 도구 ${turn.tool_calls}회` }),
    el("button", { class: "linkbtn", type: "button", onclick: () => showPane("trace"), text: "과정 보기" }),
  );
  if (scroll) {
    showPane("trace");
    if (window.matchMedia("(max-width: 1080px)").matches) $("#demo-col").scrollIntoView({ behavior: "smooth", block: "start" });
    else $("#demo-col").scrollTop = 0;
  } else if (currentPane() !== "trace") {
    $("#trace-dot").classList.remove("hidden");
    strip.classList.remove("hidden");
  }
}

// ---- 데모 도구 -----------------------------------------------------------------------

function renderCustomers() {
  const row = clear($("#cust-row"));
  if (state.customers.length === 0) {
    row.append(el("div", { class: "empty", text: "예시 고객을 불러오지 못했습니다." }));
    return;
  }
  for (const c of state.customers) {
    row.append(
      el(
        "button",
        { class: `chip ${c.customer_id === state.customerId ? "on" : ""}`, type: "button", dataset: { customer: c.customer_id }, onclick: () => switchCustomer(c.customer_id) },
        c.name,
        el("span", { class: "who", text: c.region }),
      ),
    );
  }
  const list = clear($("#order-list"));
  const current = customer();
  for (const order of current ? current.orders : []) {
    list.append(
      el(
        "li",
        {},
        el(
          "button",
          { type: "button", title: "주문번호를 입력창에 넣기", onclick: () => insertText(order.order_id) },
          el("span", { class: "oid", text: order.order_id }),
          el("span", { class: "oname", text: `${order.items} · ${won(order.paid_total)}` }),
          el("span", { class: "pill", text: order.status_label }),
        ),
      ),
    );
  }
  if (current) list.append(el("li", {}, exampleBadge("예시 주문 — 실제 주문이 아닙니다")));
}

async function reloadCustomers() {
  try {
    state.customers = (await api("/v1/demo/customers")).customers;
    renderCustomers();
  } catch {
    /* 목록 갱신 실패는 무시한다 */
  }
}

function switchCustomer(customerId, { silent = false } = {}) {
  if (customerId === state.customerId && !state.readOnly) return;
  state.customerId = customerId;
  sessionStorage.setItem("bd.customer", customerId);
  renderCustomers();
  renderScenarios();
  startNew();
  if (!silent) toast(`${customer().name} 님으로 새 대화를 시작했습니다.`);
}

function insertText(text) {
  if (input.disabled) return;
  input.value = input.value ? `${input.value.replace(/\s+$/, "")} ${text} ` : `${text} `;
  onInput();
  input.focus();
}

function renderScenarios() {
  const box = clear($("#scenarios"));
  const names = Object.fromEntries(state.customers.map((c) => [c.customer_id, c.name]));
  for (const group of SCENARIOS) {
    box.append(
      el(
        "div",
        { class: "scn-group" },
        el("h4", { text: group.group }),
        group.hint ? el("p", { class: "hint", style: { marginBottom: "6px" }, text: group.hint }) : null,
        el(
          "div",
          { class: "chips" },
          group.items.map((item) =>
            el(
              "button",
              { class: "chip", type: "button", onclick: () => useScenario(item) },
              item.c && item.c !== state.customerId ? el("span", { class: "who", text: names[item.c] || item.c }) : null,
              item.t,
              item.note ? el("span", { class: "who", text: item.note }) : null,
            ),
          ),
        ),
      ),
    );
  }
}

function useScenario(item) {
  if (state.readOnly) startNew();
  if (item.c && item.c !== state.customerId) switchCustomer(item.c);
  else if (state.conv && state.conv.status !== "open") startNew();
  if (input.disabled) {
    toast("지금은 메시지를 보낼 수 없는 상태입니다.");
    return;
  }
  input.value = item.t;
  onInput();
  input.focus();
  if (window.matchMedia("(max-width: 1080px)").matches) $(".chat-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function renderFaults(current) {
  let mode = current;
  if (!mode) {
    try {
      mode = (await api("/v1/demo/sandbox")).fault;
    } catch {
      mode = "none";
    }
  }
  const seg = clear($("#fault-seg"));
  for (const fault of FAULTS) {
    seg.append(
      el("button", {
        type: "button",
        class: `${fault.mode === mode ? "on" : ""} ${fault.mode === mode && fault.mode !== "none" ? "bad" : ""}`,
        dataset: { fault: fault.mode },
        "aria-pressed": fault.mode === mode ? "true" : "false",
        onclick: () => setFault(fault.mode),
        text: fault.label,
      }),
    );
  }
  $("#fault-note").textContent = (FAULTS.find((fault) => fault.mode === mode) || FAULTS[0]).note;
}

async function setFault(mode) {
  try {
    const result = await api("/v1/demo/fault", { method: "PUT", body: { mode } });
    renderFaults(result.fault);
  } catch (error) {
    toast(error.message);
  }
}

async function resetSandbox() {
  try {
    await api("/v1/demo/reset", { method: "POST" });
    await Promise.all([renderFaults(), reloadCustomers()]);
    toast("예시 주문 데이터를 처음 상태로 되돌렸습니다.");
  } catch (error) {
    toast(error.message);
  }
}

async function loadRecordings() {
  const list = clear($("#rec-list"));
  try {
    const { conversations } = await api("/v1/conversations?source=recorded&limit=50");
    if (conversations.length === 0) {
      list.append(el("li", { class: "empty", text: "이 서버에는 저장된 상담 기록이 아직 없습니다." }));
      return;
    }
    const count = $("#rec-count");
    count.textContent = String(conversations.length);
    count.classList.remove("hidden");
    for (const conv of conversations) {
      list.append(
        el(
          "li",
          {},
          el(
            "a",
            { href: `/chat?conversation=${encodeURIComponent(conv.id)}` },
            el("b", { text: conv.title || conv.first_message || "제목 없는 기록" }),
            el("span", { class: "small muted", text: `${conv.customer_name} · 문의 ${conv.turn_count}번${conv.handoff ? " · 상담원 인계" : ""} · ${ago(conv.created_at)}` }),
          ),
        ),
      );
    }
  } catch {
    list.append(el("li", { class: "empty", text: "저장된 상담 기록을 불러오지 못했습니다." }));
  }
}

init();
