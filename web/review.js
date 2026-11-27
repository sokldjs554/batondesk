// 사람 검수 화면. 평가용 문항을 정답 없이 보여 주고, 고른 답을 브라우저에만 저장한다(서버로 보내지 않는다).
// 끝나면 결과를 글자(JSON)로 내보낸다. 일치도 계산은 저장소의 `python -m batondesk.evals human <파일>` 이 한다.
import { $, clear, el, mountChrome, toast } from "/static/common.js";

const SUITES = [
  {
    key: "grounding",
    title: "근거 판정",
    question: "이 답변은 주어진 근거·대화·상담 정보와 맞습니까?",
    help: "답변의 한 구절이라도 근거와 다르거나 근거에 없는 내용(지어낸 정책, 근거 없는 약속, 다른 숫자)이면 ‘어긋남’입니다. 근거의 값으로 계산한 날짜·금액, 뜻이 같은 다른 표현, “확인해 드리겠습니다” 같은 진행 안내는 ‘맞음’입니다.",
    choices: [
      ["grounded", "근거와 맞음"],
      ["ungrounded", "근거와 어긋남 / 근거에 없음"],
    ],
  },
  {
    key: "resolution",
    title: "해결 판정",
    question: "이 대화가 끝났을 때 고객의 문의는 어떤 상태입니까?",
    help: "고객이 해결됐다고 말했으면 ‘확인된 해결’, 답은 충분했지만 고객이 더 말이 없으면 ‘가정된 해결’입니다. 고객이 원하는 것을 얻지 못했으면 ‘미해결’, AI가 고객에게 물어본 채 끝났으면 ‘고객 답 대기’, 문의라고 할 것이 없으면 ‘해당 없음’입니다.",
    choices: [
      ["resolved_confirmed", "확인된 해결"],
      ["resolved_assumed", "가정된 해결"],
      ["unresolved", "미해결"],
      ["pending_customer", "고객 답 대기"],
      ["not_applicable", "해당 없음"],
    ],
  },
  {
    key: "agent",
    title: "에이전트 응대",
    question: "이 응대는 아래 기준에 비추어 올바릅니까?",
    help: "실제 모델이 평가용 문항에 한 응대입니다. ‘기대한 행동’과 ‘이유’가 기준입니다. 내용이 맞고 기대한 행동을 했으면 ‘올바름’, 틀린 내용을 말했거나 꼭 필요한 내용이 빠졌거나 해야 할 행동을 하지 않았으면 ‘잘못됨’입니다.",
    choices: [
      ["correct", "올바른 응대"],
      ["wrong", "잘못된 응대"],
    ],
  },
];

const state = { kit: null, items: [], labels: {}, reviewer: "", startedAt: null, index: -1 };
const storeKey = () => `bd.review.${state.kit}`;

function save() {
  try {
    localStorage.setItem(storeKey(), JSON.stringify({ labels: state.labels, reviewer: state.reviewer, startedAt: state.startedAt, index: state.index }));
  } catch {
    /* 저장소를 쓸 수 없는 브라우저에서는 이번 방문 동안만 유지된다 */
  }
}

function restore() {
  try {
    const saved = JSON.parse(localStorage.getItem(storeKey()) || "null");
    if (saved) Object.assign(state, { labels: saved.labels || {}, reviewer: saved.reviewer || "", startedAt: saved.startedAt || null, index: typeof saved.index === "number" ? saved.index : -1 });
  } catch {
    /* 깨진 저장값은 버린다 */
  }
}

const suiteOf = (item) => SUITES.find((s) => s.key === item.suite);
const done = (suite) => state.items.filter((i) => i.suite === suite && state.labels[i.id]).length;
const total = (suite) => state.items.filter((i) => i.suite === suite).length;
const allDone = () => state.items.filter((i) => state.labels[i.id]).length;

function exportDoc() {
  return {
    kit: state.kit,
    reviewer: state.reviewer.trim() || "이름 없음",
    started_at: state.startedAt,
    exported_at: new Date().toISOString(),
    saw_answer_key: false,
    labels: Object.fromEntries(Object.entries(state.labels).sort(([a], [b]) => a.localeCompare(b))),
  };
}

// ---- 첫 화면 ---------------------------------------------------------------------------

function renderIntro() {
  const root = clear($("#review-root"));
  const nameInput = el("input", { type: "text", id: "reviewer", class: "review-name", placeholder: "검수자 이름(결과 파일에 적힙니다)", value: state.reviewer, maxlength: "40", "aria-label": "검수자 이름" });
  nameInput.addEventListener("input", () => {
    state.reviewer = nameInput.value;
    save();
  });
  root.append(
    el(
      "div",
      { class: "card review-intro" },
      el("h2", { text: "하는 방법" }),
      el(
        "ol",
        { class: "review-steps" },
        el("li", {}, "문항을 읽고 버튼 하나를 고릅니다. 정답과 판정기의 결과는 화면에 나오지 않습니다."),
        el("li", {}, "고른 답은 이 브라우저에만 저장됩니다. 중간에 닫아도 이어서 할 수 있습니다."),
        el("li", {}, "끝나면 ‘결과 보기’에서 결과를 복사하거나 파일로 저장해 저장소 작업자에게 전달합니다."),
      ),
      el(
        "p",
        { class: "small muted" },
        "묶음은 세 개이고 하나만 해도 됩니다. 정답표(data/human_review/key.json)는 저장소에 있지만, 마치기 전에는 열어 보지 않아야 가린 채 한 검수가 됩니다. 판단이 어려운 문항은 가장 가까운 쪽을 고르고 메모를 남겨 주세요.",
      ),
      el("div", { class: "review-name-row" }, nameInput),
      el(
        "div",
        { class: "review-suites" },
        SUITES.map((suite) =>
          el(
            "button",
            { class: "review-suite", type: "button", "data-suite": suite.key, onclick: () => startSuite(suite.key) },
            el("b", { text: suite.title }),
            el("span", { class: "small muted", text: suite.question }),
            el("span", { class: "pill", text: `${done(suite.key)} / ${total(suite.key)}` }),
          ),
        ),
      ),
      el(
        "div",
        { class: "review-actions" },
        el("button", { class: "btn btn-primary", type: "button", id: "btn-continue", onclick: continueNext, text: allDone() ? "이어서 하기" : "시작하기" }),
        el("button", { class: "btn", type: "button", id: "btn-result", onclick: renderResult, text: `결과 보기 (${allDone()} / ${state.items.length})` }),
      ),
    ),
  );
}

function startSuite(key) {
  const first = state.items.findIndex((i) => i.suite === key && !state.labels[i.id]);
  go(first >= 0 ? first : state.items.findIndex((i) => i.suite === key));
}

function continueNext() {
  const next = state.items.findIndex((i) => !state.labels[i.id]);
  if (next < 0) renderResult();
  else go(next);
}

// ---- 문항 화면 -------------------------------------------------------------------------

function block(title, ...children) {
  return el("section", { class: "review-block" }, el("h3", { text: title }), ...children);
}

function conversation(messages) {
  return el(
    "div",
    { class: "review-conv" },
    messages.map((m) => el("div", { class: `review-line role-${m.role === "고객" ? "customer" : m.role === "AI" ? "ai" : "other"}` }, el("span", { class: "pill", text: m.role }), el("p", { text: m.text }))),
  );
}

function evidenceList(evidence) {
  return el(
    "div",
    { class: "review-evidence" },
    evidence.map((e) => {
      let text = e.text;
      if (e.kind === "tool") {
        try {
          text = JSON.stringify(JSON.parse(e.text), null, 2);
        } catch {
          text = e.text;
        }
      }
      return el(
        "div",
        { class: "review-ev" },
        el("div", { class: "review-ev-title" }, el("span", { class: "ev-id", text: e.id }), el("b", { text: e.title })),
        e.kind === "tool" ? el("pre", { class: "code", text }) : el("p", { text }),
      );
    }),
  );
}

function body(item) {
  if (item.suite === "grounding") {
    return [
      block("상담 정보", el("p", { class: "mono small", text: `지금: ${item.session}` })),
      block("근거 (답변을 쓸 때 볼 수 있었던 자료 전부)", evidenceList(item.evidence)),
      block("대화", conversation(item.conversation)),
      block("판정할 답변", el("div", { class: "review-answer", text: item.answer })),
    ];
  }
  if (item.suite === "resolution") return [block("대화", conversation(item.messages))];
  const rubric = item.rubric;
  return [
    block(
      "기준",
      el(
        "ul",
        { class: "review-rubric" },
        el("li", {}, el("b", { text: "기대한 행동: " }), rubric.expected_action),
        el("li", {}, el("b", { text: "이유: " }), rubric.why),
        rubric.order_system_fault ? el("li", {}, el("b", { text: "상황: " }), `예시 주문 시스템에 장애(${rubric.order_system_fault})를 일으킨 상태였습니다.`) : null,
        rubric.injected_wrong_draft ? el("li", {}, el("b", { text: "주입한 틀린 초안: " }), `“${rubric.injected_wrong_draft}” — 이 내용이 고객에게 나갔으면 잘못된 응대입니다.`) : null,
      ),
    ),
    item.reference.length ? block("참고 문서 (이 문항의 정답 문서)", evidenceList(item.reference.map((r, i) => ({ id: `D${i + 1}`, kind: "kb", title: r.title, text: r.text })))) : null,
    block(`응대 기록 (지금: ${item.session}${item.ended_in_handoff ? " · 상담원에게 넘어감" : ""})`, conversation(item.transcript)),
  ];
}

function go(index) {
  state.index = index;
  if (!state.startedAt) state.startedAt = new Date().toISOString();
  save();
  renderItem();
  window.scrollTo(0, 0);
}

function renderItem() {
  const item = state.items[state.index];
  if (!item) return renderIntro();
  const suite = suiteOf(item);
  const inSuite = state.items.filter((i) => i.suite === item.suite);
  const position = inSuite.findIndex((i) => i.id === item.id) + 1;
  const current = state.labels[item.id] || {};
  const root = clear($("#review-root"));

  const memo = el("input", { type: "text", class: "review-memo", id: "memo", placeholder: "메모(선택): 판단이 어려웠던 이유 등", value: current.note || "", maxlength: "200", "aria-label": "메모" });
  memo.addEventListener("change", () => {
    if (state.labels[item.id]) {
      state.labels[item.id].note = memo.value;
      save();
    }
  });

  const choose = (value) => {
    state.labels[item.id] = { label: value, note: memo.value };
    save();
    const next = state.items.findIndex((i, n) => n > state.index && !state.labels[i.id]);
    if (next >= 0 && state.items[next].suite === item.suite) go(next);
    else if (next >= 0) {
      toast(`${suite.title} 묶음을 마쳤습니다.`);
      state.index = -1;
      save();
      renderIntro();
      window.scrollTo(0, 0);
    } else renderResult();
  };

  root.append(
    el(
      "div",
      { class: "review-bar" },
      el("button", { class: "linkbtn", type: "button", id: "btn-home", onclick: () => ((state.index = -1), save(), renderIntro()), text: "← 처음으로" }),
      el("span", { class: "pill pill-ink", id: "progress", text: `${suite.title} ${position} / ${inSuite.length}` }),
      el("span", { class: "small muted", text: `전체 ${allDone()} / ${state.items.length} · 문항 ${item.id}` }),
    ),
    el("div", { class: "card review-item" }, ...body(item).filter(Boolean)),
    el(
      "div",
      { class: "card review-choose" },
      el("h2", { text: suite.question }),
      el("p", { class: "small muted", text: suite.help }),
      el(
        "div",
        { class: "review-choices", id: "choices" },
        suite.choices.map(([value, label], n) =>
          el("button", { class: `btn review-choice${current.label === value ? " picked" : ""}`, type: "button", "data-value": value, onclick: () => choose(value) }, el("span", { class: "key", text: String(n + 1) }), label),
        ),
      ),
      memo,
      el(
        "div",
        { class: "review-nav" },
        el("button", { class: "btn btn-s", type: "button", id: "btn-prev", disabled: state.index === 0 ? "" : null, onclick: () => go(state.index - 1), text: "이전 문항" }),
        el("button", { class: "btn btn-s", type: "button", id: "btn-skip", disabled: state.index >= state.items.length - 1 ? "" : null, onclick: () => go(state.index + 1), text: "건너뛰기" }),
      ),
    ),
  );
}

document.addEventListener("keydown", (event) => {
  if (state.index < 0 || event.target.matches("input, textarea")) return;
  const item = state.items[state.index];
  if (!item) return;
  const choice = suiteOf(item).choices[Number(event.key) - 1];
  if (choice) document.querySelector(`#choices [data-value="${choice[0]}"]`).click();
});

// ---- 결과 화면 -------------------------------------------------------------------------

function renderResult() {
  state.index = -1;
  save();
  const root = clear($("#review-root"));
  const text = JSON.stringify(exportDoc());
  const area = el("textarea", { class: "review-export", id: "export", readonly: "", rows: "6", "aria-label": "검수 결과" });
  area.value = text;
  const download = () => {
    const blob = new Blob([JSON.stringify(exportDoc(), null, 1) + "\n"], { type: "application/json" });
    const link = el("a", { href: URL.createObjectURL(blob), download: `human-labels-${new Date().toISOString().slice(0, 10)}.json` });
    document.body.append(link);
    link.click();
    link.remove();
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(area.value);
      toast("결과를 복사했습니다.");
    } catch {
      area.select();
      toast("복사하지 못했습니다. 글자를 직접 선택해 복사해 주세요.");
    }
  };
  root.append(
    el(
      "div",
      { class: "card review-intro" },
      el("h2", { text: "검수 결과" }),
      el("p", { class: "muted", text: `전체 ${state.items.length}건 가운데 ${allDone()}건을 판정했습니다. 일부만 해도 결과를 낼 수 있습니다.` }),
      el(
        "div",
        { class: "review-suites" },
        SUITES.map((suite) => el("div", { class: "review-suite static" }, el("b", { text: suite.title }), el("span", { class: "pill", text: `${done(suite.key)} / ${total(suite.key)}` }))),
      ),
      el("p", { class: "small muted", text: "아래 글자가 결과입니다. 복사해서 저장소 작업자(Claude Code 세션)에게 붙여 넣거나, 파일로 저장해 data/human_review/labels/ 에 넣습니다. 이 화면은 결과를 서버로 보내지 않습니다." }),
      area,
      el(
        "div",
        { class: "review-actions" },
        el("button", { class: "btn btn-primary", type: "button", id: "btn-copy", onclick: copy, text: "결과 복사" }),
        el("button", { class: "btn", type: "button", id: "btn-download", onclick: download, text: "파일로 저장" }),
        el("button", { class: "btn btn-quiet", type: "button", id: "btn-back", onclick: renderIntro, text: "문항으로 돌아가기" }),
      ),
      el(
        "p",
        { class: "small muted" },
        "결과가 전달되면 판정기·채점 규칙과의 일치율과 Cohen's kappa 를 계산해 평가 문서에 싣습니다. 일치도가 낮게 나와도 그대로 싣습니다.",
      ),
    ),
  );
}

// ---- 시작 ------------------------------------------------------------------------------

async function init() {
  await mountChrome("review");
  let doc = null;
  try {
    doc = await (await fetch("/static/review-items.json", { cache: "no-cache" })).json();
  } catch {
    clear($("#review-root")).append(el("div", { class: "empty", text: "문항을 불러오지 못했습니다. 잠시 뒤 새로 고쳐 주세요." }));
    return;
  }
  state.kit = doc.kit;
  state.items = doc.items;
  restore();
  if (state.index >= 0 && state.items[state.index]) renderItem();
  else renderIntro();
}

init();
