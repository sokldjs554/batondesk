// BatonDesk 화면 공용 도구: API 호출, DOM 생성, 표기 형식, 위쪽 안내 띠·내비게이션·바닥글.
// 고객·모델이 쓴 글은 언제나 textContent 로만 넣는다(innerHTML 을 쓰지 않는다).

export const REPO_URL = "https://github.com/sokldjs554/batondesk";
export const NOTICE_TEXT = "주문·고객·정책은 가상의 예시 쇼핑몰 '바통마켓'의 예시 데이터입니다";

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "network_error", "서버에 연결하지 못했습니다. 네트워크 상태를 확인한 뒤 다시 시도해 주세요.");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (!res.ok) {
    const err = data && data.error;
    const message = (err && err.message) || (data && data.detail ? "요청 형식이 올바르지 않습니다." : `요청을 처리하지 못했습니다 (${res.status}).`);
    throw new ApiError(res.status, (err && err.code) || `http_${res.status}`, message);
  }
  return data;
}

export function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(node, child);
    else node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

// node.append 와 달리 null·false 를 건너뛴다(기본 append 는 null 을 "null" 글자로 넣는다).
export function put(node, ...children) {
  append(node, children);
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export const $ = (selector, root = document) => root.querySelector(selector);

// ---- 표기 형식 ----------------------------------------------------------------------

export function won(n) {
  return n === null || n === undefined ? "—" : `${Number(n).toLocaleString("ko-KR")}원`;
}

export function int(n) {
  return n === null || n === undefined ? "—" : Number(n).toLocaleString("ko-KR");
}

export function pct(x, digits = 1) {
  if (x === null || x === undefined) return "—";
  return `${(Number(x) * 100).toFixed(digits).replace(/\.0+$/, "")}%`;
}

export function ms(x) {
  if (x === null || x === undefined) return "—";
  const value = Number(x);
  if (value >= 10000) return `${(value / 1000).toFixed(0)}초`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}초`;
  return `${Math.round(value)}ms`;
}

export function usd(x) {
  if (x === null || x === undefined) return "—";
  const value = Number(x);
  if (value === 0) return "$0";
  return `$${value < 1 ? value.toFixed(4) : value.toFixed(2)}`;
}

export function clock(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function dateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function ago(iso) {
  if (!iso) return "";
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "방금";
  if (seconds < 3600) return `${Math.round(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}시간 전`;
  return `${Math.round(seconds / 86400)}일 전`;
}

// ---- 배지 ---------------------------------------------------------------------------

export function engineBadge(release) {
  const engine = release && release.engine ? release.engine.engine : null;
  if (engine === "live") return el("span", { class: "badge badge-live", text: `실제 모델 호출 · ${release.engine.agent_model}` });
  if (engine === "off") return el("span", { class: "badge badge-off", text: "모델 호출 꺼짐 · 저장된 기록만" });
  if (engine === "rule-engine" || engine === "scripted") return el("span", { class: "badge badge-rule", text: "규칙 엔진 (LLM 아님)" });
  return el("span", { class: "badge badge-off", text: "서버 상태를 확인하지 못했습니다" });
}

// 대화 한 건이 어디서 나온 것인지: 실제 모델 호출 / 저장된 상담 기록 / 규칙 엔진
export function sourceBadge(source, extra) {
  if (source === "live") return el("span", { class: "badge badge-live", text: extra ? `실제 모델 호출 · ${extra}` : "실제 모델 호출" });
  if (source === "recorded") return el("span", { class: "badge badge-recorded", text: "저장된 상담 기록" });
  if (source === "anthropic") return el("span", { class: "badge badge-live", text: "실제 모델 호출" });
  return el("span", { class: "badge badge-rule", text: "규칙 엔진 (LLM 아님)" });
}

export function exampleBadge(text = "예시 주문 시스템") {
  return el("span", { class: "badge badge-example plain", text });
}

// ---- 화면 틀 ------------------------------------------------------------------------

const NAV = [
  { href: "/", key: "home", label: "소개" },
  { href: "/chat", key: "chat", label: "고객", wide: " 화면" },
  { href: "/desk", key: "desk", label: "상담원", wide: " 화면" },
  { href: "/ops", key: "ops", label: "운영", wide: " 화면" },
];

export async function mountChrome(active, { nav = true } = {}) {
  const notice = el(
    "div",
    { class: "notice", role: "note", id: "notice" },
    el("span", { class: "notice-tag", text: "예시 데이터" }),
    el("span", { class: "notice-text", text: NOTICE_TEXT }),
    el("span", { id: "engine-slot" }, el("span", { class: "badge badge-rule plain", text: "서버 확인 중…" })),
  );
  document.body.prepend(notice);
  if (nav) {
    const top = el(
      "header",
      { class: "topbar" },
      el("a", { class: "brand", href: "/" }, el("span", { class: "brand-mark" }), "BatonDesk"),
      el(
        "nav",
        { class: "nav", "aria-label": "화면 이동" },
        // 좁은 화면에서는 " 화면"을 감춰 상표와 메뉴를 한 줄에 둔다(읽어 주는 글은 그대로 "고객 화면").
        NAV.map((item) =>
          el("a", { href: item.href, class: item.key === active ? "active" : null, "aria-current": item.key === active ? "page" : null }, item.label, item.wide ? el("span", { class: "nav-wide", text: item.wide }) : null),
        ),
      ),
    );
    notice.after(top);
  }
  const footer = el("footer", { class: "footer", id: "footer" }, el("span", { text: "BatonDesk" }));
  document.body.append(footer);

  let release = null;
  try {
    release = await api("/v1/release");
  } catch {
    release = null;
  }
  clear($("#engine-slot")).append(engineBadge(release));
  clear(footer);
  if (release) {
    footer.append(
      el("span", { text: "BatonDesk — 쇼핑몰 고객센터 AI" }),
      el("span", {}, "커밋 ", el("span", { class: "mono", id: "footer-sha", text: String(release.git_sha).slice(0, 7) })),
      el("span", {}, "에이전트 프롬프트 ", el("span", { class: "mono", id: "footer-prompt", text: release.prompts.agent.id })),
      el("span", { text: `지식 문서 ${release.knowledge_base.documents}건` }),
      el("a", { href: "/review", text: "사람 검수" }),
      el("a", { href: "/docs", text: "API 문서" }),
      el("a", { href: REPO_URL, text: "GitHub" }),
    );
  } else {
    footer.append(el("span", { text: "서버 정보를 불러오지 못했습니다." }));
  }
  document.documentElement.style.setProperty("--notice-h", `${notice.offsetHeight}px`);
  return release;
}

let toastTimer = null;
export function toast(message) {
  document.querySelectorAll(".toast").forEach((node) => node.remove());
  const node = el("div", { class: "toast", role: "status", text: message });
  document.body.append(node);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.remove(), 3200);
}

// ---- 근거·실행 기록 표시(고객 화면과 상담원 화면이 함께 쓴다) --------------------------------

const DOC_TYPE = { policy: "정책", notice: "공지", faq: "자주 묻는 질문", guide: "안내" };

const KEY_LABEL = {
  order_id: "주문번호",
  status: "상태 코드",
  status_label: "주문 상태",
  ordered_at: "주문 일시",
  payment_method: "결제수단",
  payment_due_at: "입금 기한",
  items: "상품",
  item_id: "상품 번호",
  item_name: "상품",
  name: "상품명",
  category: "분류",
  qty: "수량",
  unit_price: "단가",
  items_total: "상품 금액",
  shipping_fee: "배송비",
  remote_area_fee: "도서산간 추가 배송비",
  discount: "할인",
  paid_total: "결제 금액",
  shipment: "배송",
  carrier: "택배사",
  tracking_no: "운송장 번호",
  shipped: "출고 여부",
  shipped_at: "출고일",
  delivered_at: "배송 완료일",
  events: "이동 기록",
  at: "시각",
  location: "위치",
  last_event_at: "마지막 이동",
  days_since_last_event: "마지막 이동 뒤 지난 날",
  refund: "환불",
  amount: "금액",
  method: "수단",
  processed_at: "처리일",
  reason: "사유",
  return_request: "반품 접수",
  requested_at: "접수일",
  pickup_status: "회수 상태",
  pickup_expected_at: "회수 예정일",
  allowed: "가능 여부",
  code: "판정 코드",
  message: "안내",
  refund_amount: "환불 예상 금액",
  return_fee: "반품 배송비",
  deadline_date: "신청 기한",
  delivered_date: "받은 날",
  confirmation_code: "확인 코드",
  dry_run: "미리 확인(실행 안 함)",
  executed: "실행됨",
  found: "조회 결과",
  needs_agent_review: "상담원 확인 필요",
  photo_required: "사진 필요",
  orders: "주문 목록",
  customer_id: "고객번호",
  item_names: "상품",
};
const MONEY_KEYS = new Set(["unit_price", "items_total", "shipping_fee", "remote_area_fee", "discount", "paid_total", "amount", "refund_amount", "return_fee"]);
const HIDDEN_KEYS = new Set(["next_step"]);

function scalar(key, value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "예" : "아니오";
  if (typeof value === "number" && MONEY_KEYS.has(key)) return won(value);
  return String(value);
}

export function keyValueView(data) {
  if (data === null || typeof data !== "object") return el("span", { text: scalar("", data) });
  const dl = el("dl", { class: "kv-table" });
  for (const [key, value] of Object.entries(data)) {
    if (HIDDEN_KEYS.has(key)) continue;
    dl.append(el("dt", { text: KEY_LABEL[key] || key }));
    const dd = el("dd");
    if (Array.isArray(value)) {
      if (value.length === 0) dd.textContent = "없음";
      else if (value.every((item) => item === null || typeof item !== "object")) dd.textContent = value.join(", ");
      else dd.append(el("div", { class: "list-gap" }, value.map((item) => el("div", { class: "kv-nested" }, keyValueView(item)))));
    } else if (value !== null && typeof value === "object") {
      dd.append(el("div", { class: "kv-nested" }, keyValueView(value)));
    } else {
      dd.textContent = scalar(key, value);
    }
    dl.append(dd);
  }
  return dl;
}

export function evidenceValidity(ev) {
  const meta = ev.meta || {};
  if (ev.kind !== "kb") return null;
  const range = meta.effective_to ? `${meta.effective_from} ~ ${meta.effective_to}` : `${meta.effective_from}부터`;
  return el(
    "div",
    { class: "msg-actions" },
    el("span", { class: `pill ${meta.in_effect ? "pill-ok" : "pill-bad"}`, text: meta.in_effect ? "현재 적용 중" : "지금은 적용되지 않는 문서" }),
    el("span", { class: "pill", text: DOC_TYPE[meta.doc_type] || meta.doc_type || "문서" }),
    el("span", { class: "small muted", text: `적용 기간 ${range}` }),
  );
}

export function openEvidenceSheet(ev) {
  closeSheet();
  const isTool = ev.kind === "tool";
  let parsed = null;
  if (isTool) {
    try {
      parsed = JSON.parse(ev.text);
    } catch {
      parsed = null;
    }
  }
  const meta = ev.meta || {};
  const body = el("div", { class: "sheet-body" });
  if (isTool) {
    body.append(
      el("div", { class: "msg-actions" }, exampleBadge("예시 주문 시스템 조회 결과"), el("span", { class: `pill ${meta.status === "ok" ? "pill-ok" : "pill-warn"}`, text: meta.status === "ok" ? "조회 성공" : "찾지 못함" })),
      el("p", { class: "small muted", text: "실제 주문 시스템이 아니라 데모용 예시 API가 돌려준 값입니다." }),
      el("div", { class: "passage" }, parsed ? keyValueView(parsed) : ev.text),
      el("p", { class: "small muted" }, "호출: ", el("span", { class: "mono", text: `${meta.tool}(${Object.entries(meta.args || {}).map(([k, v]) => `${k}=${v}`).join(", ")})` })),
    );
  } else {
    body.append(
      evidenceValidity(ev),
      el("div", { class: "passage", text: ev.text }),
      el("p", { class: "small muted" }, "문서 ", el("span", { class: "mono", text: meta.chunk_id || "" }), meta.query ? ` · 검색 질의 "${meta.query}"` : ""),
    );
  }
  const sheet = el(
    "div",
    { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": "근거 상세" },
    el(
      "div",
      { class: "sheet-head" },
      el("div", {}, el("span", { class: "pill pill-ink", text: `근거 ${ev.id}` }), el("h3", { text: isTool ? "주문 시스템 조회 결과" : meta.doc_title ? `${meta.doc_title} › ${meta.heading}` : ev.title })),
      el("button", { class: "btn btn-s", type: "button", id: "sheet-close", onclick: closeSheet, text: "닫기" }),
    ),
    body,
  );
  const back = el("div", { class: "sheet-back", id: "sheet-back", onclick: (event) => event.target === back && closeSheet() }, sheet);
  document.body.append(back);
  document.addEventListener("keydown", escClose);
  $("#sheet-close").focus();
}

function escClose(event) {
  if (event.key === "Escape") closeSheet();
}

export function closeSheet() {
  const back = $("#sheet-back");
  if (back) back.remove();
  document.removeEventListener("keydown", escClose);
}

export function sourceChips(citations) {
  if (!citations || citations.length === 0) return null;
  return el(
    "div",
    { class: "sources", "aria-label": "답변의 근거" },
    citations.map((ev) => {
      const meta = ev.meta || {};
      const title = ev.kind === "tool" ? `주문 시스템 · ${meta.tool || "조회"}` : meta.doc_title ? `${meta.doc_title} › ${meta.heading}` : ev.title;
      return el(
        "button",
        { class: `source-chip ${ev.kind === "tool" ? "tool" : ""}`, type: "button", title: "근거 보기", onclick: () => openEvidenceSheet(ev) },
        el("span", { class: "sid", text: ev.id }),
        el("span", { class: "stitle", text: title }),
      );
    }),
  );
}

const STAGE_LABEL = { retrieval: "지식 검색", llm_agent: "에이전트 모델", llm_checks: "판정 모델", tools: "주문 시스템" };
const PURPOSE_LABEL = { agent: "에이전트", grounding: "근거 판정", resolution: "해결 판정" };
const TOOL_STATUS = {
  ok: ["성공", "pill-ok"],
  not_found: ["찾지 못함", "pill-warn"],
  timeout: ["시간 초과", "pill-bad"],
  unavailable: ["시스템 오류", "pill-bad"],
  rejected: ["거부됨", "pill-bad"],
  invalid: ["잘못된 인자", "pill-warn"],
  denied: ["실행 거부", "pill-bad"],
};

export function toolStatusPill(status) {
  const [label, cls] = TOOL_STATUS[status] || [status, "pill"];
  return el("span", { class: `pill ${cls}`, text: label });
}

export function stageBar(stage) {
  const entries = Object.entries(stage || {}).filter(([, value]) => value > 0);
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  if (!total) return null;
  return el(
    "div",
    { class: "list-gap" },
    el("div", { class: "stagebar", role: "img", "aria-label": "단계별 소요 시간" }, entries.map(([key, value]) => el("i", { class: `c-${key}`, style: { width: `${(value / total) * 100}%` } }))),
    el("div", { class: "stage-legend" }, entries.map(([key, value]) => el("span", {}, el("i", { class: `c-${key}` }), `${STAGE_LABEL[key] || key} ${ms(value)}`))),
  );
}

function stepView(step) {
  const head = el("div", { class: "step-head" });
  const box = el("div", { class: "step" }, head);
  const meta = (text) => head.append(el("span", { class: "meta", text }));
  if (step.type === "retrieval") {
    head.append(el("span", { class: "step-kind", text: "검색" }), "지식 문서 검색");
    meta(`${ms(step.latency_ms)} · ${step.hits.length}건`);
    box.append(el("div", { class: "kv", text: `질의: ${step.query}` }));
    if (step.hits.length) box.append(el("ul", {}, step.hits.map((hit) => el("li", { text: `${hit.evidence_id} ${hit.title}` }))));
  } else if (step.type === "llm") {
    head.append(el("span", { class: "step-kind k-llm", text: "모델" }), PURPOSE_LABEL[step.purpose] || step.purpose);
    meta(`${ms(step.latency_ms)}${step.attempts > 1 ? ` · ${step.attempts}번째 시도` : ""}`);
    const usage = step.usage || {};
    const engine = step.engine === "anthropic" ? step.model : "규칙 엔진(LLM 아님)";
    box.append(el("div", { class: "kv", text: `${engine} · 입력 ${int(usage.input_tokens)} / 출력 ${int(usage.output_tokens)} 토큰${step.cost_usd ? ` · 추정 ${usd(step.cost_usd)}` : ""}` }));
    if (step.tool_calls && step.tool_calls.length) box.append(el("div", { class: "kv", text: `도구 요청: ${step.tool_calls.map((call) => call.name).join(", ")}` }));
  } else if (step.type === "tool") {
    head.append(el("span", { class: "step-kind k-tool", text: "도구" }), el("span", { class: "mono", text: step.name }), toolStatusPill(step.status));
    meta(`${ms(step.latency_ms)}${step.attempts > 1 ? ` · ${step.attempts}회 시도` : ""}`);
    const args = Object.entries(step.args || {}).map(([k, v]) => `${k}=${v}`).join(", ");
    if (args) box.append(el("div", { class: "kv mono", text: args }));
  } else if (step.type === "grounding") {
    const ok = step.verdict === "grounded";
    head.append(el("span", { class: "step-kind k-check", text: "검사" }), "근거 일치 검사", el("span", { class: `pill ${ok ? "pill-ok" : "pill-bad"}`, text: ok ? "근거와 맞음" : "근거와 어긋남" }));
    meta(step.mode === "llm+rules" ? "모델 판정 + 규칙" : "규칙만");
    if (step.problems && step.problems.length) box.append(el("ul", {}, step.problems.map((problem) => el("li", { text: problem }))));
    if (step.fact_flags && step.fact_flags.length) box.append(el("div", { class: "kv", text: `근거에서 글자 그대로 찾지 못한 값: ${step.fact_flags.map((flag) => flag.surface).join(", ")}` }));
  } else if (step.type === "revise") {
    head.append(el("span", { class: "step-kind k-warn", text: "보류" }), "초안을 고객에게 보내지 않고 다시 작성");
  } else if (step.type === "fallback") {
    head.append(el("span", { class: "step-kind k-warn", text: "인계" }), "시스템이 상담원 연결로 전환");
    box.append(el("div", { class: "kv", text: `사유: ${step.reason}${step.error ? ` (${step.error})` : ""}` }));
  } else if (step.type === "citation_fix") {
    head.append(el("span", { class: "step-kind k-check", text: "검사" }), "없는 근거 ID 인용 제거");
    box.append(el("div", { class: "kv", text: step.removed.join(", ") }));
  } else if (step.type === "llm_error" || step.type === "check_error") {
    head.append(el("span", { class: "step-kind k-warn", text: "오류" }), step.type === "llm_error" ? "모델 호출 실패" : "판정 실패");
    box.append(el("div", { class: "kv", text: step.error || step.kind || "" }));
  } else if (step.type === "eval_injection") {
    head.append(el("span", { class: "step-kind k-warn", text: "평가" }), "평가용으로 초안을 바꿔치기함");
  } else {
    head.append(el("span", { class: "step-kind", text: step.type }));
  }
  return box;
}

// 턴 한 건(turns[] 의 원소)을 "이번 응답이 만들어진 과정"으로 그린다.
export function traceView(turn) {
  const detail = turn.detail || {};
  const wrap = el("div", { class: "trace" });
  const cell = (value, label) => el("div", {}, el("b", { text: value }), el("span", { text: label }));
  wrap.append(
    el(
      "div",
      { class: "trace-sum" },
      cell(ms(turn.latency_ms), "전체 시간"),
      cell(int(turn.llm_calls), "모델 호출"),
      cell(int(turn.tool_calls), "도구 호출"),
      cell(turn.cost_usd ? usd(turn.cost_usd) : "—", "추정 비용"),
    ),
  );
  const bar = stageBar(detail.stage_latency_ms);
  if (bar) wrap.append(bar);
  wrap.append(
    el(
      "div",
      { class: "small muted" },
      turn.engine === "anthropic" ? `실제 모델 호출 · ${turn.model}` : "규칙 엔진(LLM 아님)",
      ` · 프롬프트 ${turn.prompt_id} (${turn.prompt_sha})`,
      turn.error ? ` · 오류 ${turn.error}` : "",
    ),
  );
  for (const blocked of detail.blocked || []) {
    wrap.append(
      el(
        "div",
        { class: "draft" },
        el("b", { text: "고객에게 보내지 않은 초안" }),
        el("div", { class: "d-text", text: blocked.message }),
        blocked.problems && blocked.problems.length ? el("ul", {}, blocked.problems.map((problem) => el("li", { text: problem }))) : null,
      ),
    );
  }
  for (const step of detail.steps || []) wrap.append(stepView(step));
  return wrap;
}

// 메시지 한 건을 말풍선으로. 고객 화면·상담원 화면·읽기 전용 보기에서 같이 쓴다.
export function messageView(message, { onTrace, handoff, deskLink = true, compactSystem = false } = {}) {
  const payload = message.payload || {};
  const time = clock(message.created_at);
  const timeNode = () => (time ? el("span", { class: "num", text: time }) : null);
  if (message.role === "system") {
    if (payload.kind === "handoff_opened" && compactSystem) {
      return el("div", { class: "msg system", dataset: { seq: message.seq } }, el("div", { class: "sys-pill sys-handoff", text: `여기서 상담원에게 넘어왔습니다 · ${payload.reason_label || "인계"}` }));
    }
    if (payload.kind === "handoff_opened") {
      const card = el(
        "div",
        { class: "handoff-card" },
        el("h4", {}, el("span", { class: "baton", text: "→" }), "상담원에게 넘겼습니다", el("span", { class: "pill pill-ink", text: payload.reason_label || "인계" })),
        el("p", { style: { marginTop: "8px" }, text: "대화 내용과 확인한 근거가 함께 전달되었습니다. 상담원이 이어서 답할 때까지 AI는 답하지 않습니다." }),
      );
      if (handoff && handoff.summary) {
        card.append(el("details", {}, el("summary", { text: "상담원에게 전달된 요약 보기" }), el("div", { class: "passed", text: handoff.summary })));
      }
      if (deskLink) {
        card.append(el("p", { class: "small", style: { marginTop: "10px" } }, "이 데모에서는 ", el("a", { href: "/desk", target: "_blank", rel: "noopener", text: "상담원 화면" }), "을 열어 상담원 역할로 직접 답해 볼 수 있습니다."));
      }
      return el("div", { class: "msg system", dataset: { seq: message.seq } }, card);
    }
    return el("div", { class: "msg system", dataset: { seq: message.seq } }, el("div", { class: "sys-pill", text: message.text }));
  }
  if (message.role === "customer") {
    return el("div", { class: "msg customer", dataset: { seq: message.seq || "" } }, el("div", { class: "bubble", text: message.text }), time ? el("span", { class: "who-line num", text: time }) : null);
  }
  if (message.role === "agent") {
    return el(
      "div",
      { class: "msg agent", dataset: { seq: message.seq } },
      el("div", { class: "who-line" }, el("span", { class: "pill pill-warn", text: "상담원" }), el("span", { text: payload.agent || "상담원" }), timeNode()),
      el("div", { class: "bubble", text: message.text }),
    );
  }
  const node = el(
    "div",
    { class: "msg ai", dataset: { seq: message.seq } },
    el(
      "div",
      { class: "who-line" },
      el("span", { class: "pill", text: "AI 상담" }),
      payload.engine ? el("span", { text: payload.engine === "anthropic" ? "실제 모델 호출" : "규칙 엔진(LLM 아님)" }) : null,
      timeNode(),
    ),
    el("div", { class: "bubble", text: message.text }),
    sourceChips(payload.citations),
  );
  if (onTrace && payload.turn_seq) {
    node.append(el("div", { class: "msg-actions" }, el("button", { class: "linkbtn", type: "button", onclick: () => onTrace(payload.turn_seq), text: "이 응답이 만들어진 과정" })));
  }
  return node;
}
