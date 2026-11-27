// BatonDesk 화면 점검: PC(1440×900)와 모바일(390×844)에서 주요 흐름을 실제로 눌러 보고 캡처한다.
//
// 서버는 규칙 엔진(LLM 아님)으로 띄운다. 그래서 여기서 보는 것은 화면의 동작이지 답변 품질이 아니다.
//   대화와 출처 열기 · 주문 조회 · 주문 시스템 장애 → 인계 카드 · 상담원 화면에서 맡기/답장/종료 ·
//   운영 지표와 검토 대기 · 임베드 위젯 · 오류 상태(600자 초과, 종료된 대화, 끊긴 연결과 재시도)
//   + 모든 화면에서 가로 스크롤 없음, 누르는 대상 높이 ≥ 40px, 콘솔 오류 없음
//
//   tests/e2e/run.sh            # 서버를 띄우고 이 스크립트를 실행
//   BASE=http://localhost:8811 SHOTS_DIR=var/ui-shots SCALE=2 node tests/e2e/ui_flows.js [desktop|mobile]
const { chromium } = require("playwright");
const path = require("path");

const BASE = process.env.BASE || "http://localhost:8811";
const OUT = path.resolve(process.env.SHOTS_DIR || path.join(__dirname, "..", "..", "var", "ui-shots"));
require("fs").mkdirSync(OUT, { recursive: true });
const SCALE = Number(process.env.SCALE || 1);
let ACTIVE_AGENT = null;
const VIEWPORTS = { desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } };
const only = process.argv[2];

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

async function noHScroll(page, label) {
  const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  check(`${label}: 가로 스크롤 없음`, sw <= cw + 1, `scrollWidth=${sw} clientWidth=${cw}`);
}

async function smallTargets(page, label) {
  const small = await page.evaluate(() => {
    const out = [];
    for (const node of document.querySelectorAll("button, a.btn, .nav a, .footer a, .chip, .tab, select, input:not([type=checkbox]), textarea")) {
      const r = node.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      const style = getComputedStyle(node);
      if (style.visibility === "hidden" || style.display === "none") continue;
      if (r.height < 39.5) out.push(`${node.tagName}.${node.className}:${Math.round(r.height)}px "${(node.textContent || "").trim().slice(0, 14)}"`);
    }
    return out;
  });
  check(`${label}: 누르는 대상 높이 ≥ 40px`, small.length === 0, small.slice(0, 6).join(" | "));
}

async function ask(page, text) {
  const before = await page.locator("#chat-log .msg.ai:not(#greeting), #chat-log .handoff-card").count();
  await page.fill("#input", text);
  await page.click("#btn-send");
  await page.waitForFunction((n) => document.querySelectorAll("#chat-log .msg.ai:not(#greeting), #chat-log .handoff-card").length > n && document.querySelector("#chat-status").classList.contains("hidden"), before, { timeout: 20000 });
}

async function run(name, viewport) {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport, deviceScaleFactor: SCALE, locale: "ko-KR" });
  const errors = [];
  const watch = (page, tag) => {
    page.on("console", (msg) => {
      if (msg.type() !== "error") return;
      const where = msg.location().url || "";
      // 일부러 만든 두 가지는 뺀다: 끊어 본 스트림 요청, 아직 없는 /v1/evals(404).
      if (where.includes("/messages/stream") || where.includes("/v1/evals")) return;
      errors.push(`${tag}: ${msg.text()} @ ${where}`);
    });
    page.on("pageerror", (err) => errors.push(`${tag}: ${err.message}`));
  };
  const shot = async (page, file, full = true) => {
    // 전체 캡처는 맨 위에서 찍는다(붙어 있는 안내 띠가 중간에 찍히지 않게).
    if (full) await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(OUT, `${name}-${file}.png`), fullPage: full });
  };
  const L = (s) => `[${name}] ${s}`;

  // 1. 첫 화면
  const page = await context.newPage();
  watch(page, "main");
  await page.goto(`${BASE}/`);
  await page.waitForSelector("#footer-sha");
  check(L("첫 화면: 문제 설명과 3단계 안내가 첫 화면 안에 있음"), await page.evaluate(() => {
    const h1 = document.querySelector(".hero h1").getBoundingClientRect();
    return h1.top < window.innerHeight && document.querySelectorAll(".try-steps li").length === 3;
  }));
  check(L("첫 화면: 엔진 배지"), (await page.textContent("#engine-slot")).includes("규칙 엔진 (LLM 아님)"));
  check(L("첫 화면: 바닥글에 커밋·프롬프트"), (await page.textContent("#footer-prompt")) === ACTIVE_AGENT && (await page.textContent("#footer-sha")).length > 0);
  check(L("첫 화면: 진입 카드 3개·흐름 7단계·구분 3개"), (await page.locator(".entry").count()) === 3 && (await page.locator(".flow li").count()) === 7 && (await page.locator(".legend .panel").count()) === 3);
  await noHScroll(page, L("첫 화면"));
  await smallTargets(page, L("첫 화면"));
  await shot(page, "01-landing");
  await shot(page, "01-landing-fold", false);

  // 2. 고객 화면: 문서 질문 + 출처
  await page.goto(`${BASE}/chat?customer=C-1001`);
  await page.waitForSelector("#greeting");
  await page.evaluate(() => sessionStorage.clear());
  await page.reload();
  await page.waitForSelector("#greeting");
  await shot(page, "02-chat-empty");
  await ask(page, "무료배송으로 받은 상품을 단순변심으로 반품하면 배송비가 얼마인가요?");
  check(L("(a) 문서 질문에 AI 답변과 출처 표시"), (await page.locator("#chat-log .msg.ai:not(#greeting) .source-chip").count()) >= 1);
  check(L("(a) 실행 과정 패널이 채워짐"), (await page.locator("#trace .step").count()) >= 3);
  check(L("(a) 해결 여부 버튼 표시"), (await page.locator("#fb-yes").count()) === 1);
  check(L("(a) 방금 응답 요약 줄 표시"), (await page.locator("#last-turn").isVisible()) && (await page.textContent("#last-turn")).includes("근거와 맞음") && !(await page.textContent("#last-turn")).includes("null"));
  await page.locator("#chat-log .msg.ai:not(#greeting) .linkbtn").first().click();
  await page.waitForFunction(() => !document.querySelector("#pane-trace").classList.contains("hidden"));
  check(L("(a) '이 응답이 만들어진 과정'을 누르면 과정 구역이 열림"), (await page.locator("#pane-trace .step").first().isVisible()));
  await shot(page, "03a-chat-trace", false);
  await page.click('#demo-tabs [data-pane="tools"]');
  await page.locator("#chat-log .source-chip").first().click();
  await page.waitForSelector(".sheet .passage");
  check(L("(a) 출처를 누르면 근거 본문과 적용 여부가 열림"), (await page.textContent(".sheet")).includes("현재 적용 중") && (await page.textContent(".sheet .passage")).length > 20);
  await shot(page, "03-chat-source", false);
  await page.click("#sheet-close");
  await page.click("#fb-yes");
  await page.waitForFunction(() => document.querySelector("#chat-log .feedback") && document.querySelector("#chat-log .feedback").textContent.includes("해결됐어요"));
  check(L("(a) 해결됐어요 기록"), true);

  // 3. 주문 조회
  await ask(page, "BM-24012 주문 어디쯤 왔나요?");
  check(L("(b) 주문 조회 답변에 주문 시스템 출처"), (await page.locator("#chat-log .source-chip.tool").count()) >= 1);
  await page.locator("#chat-log .source-chip.tool").last().click();
  await page.waitForSelector(".sheet .kv-table");
  check(L("(b) 도구 근거가 '예시 주문 시스템 조회 결과'로 표시"), (await page.textContent(".sheet")).includes("예시 주문 시스템 조회 결과") && (await page.textContent(".sheet")).includes("운송장 번호"));
  await shot(page, "04-chat-tool-source", false);
  await page.click("#sheet-close");
  await shot(page, "05-chat-after-order");
  await noHScroll(page, L("고객 화면"));
  await smallTargets(page, L("고객 화면"));

  // 4. 장애 재현 → 인계 카드
  await page.click('#fault-seg [data-fault="error"]');
  await page.waitForFunction(() => document.querySelector('#fault-seg [data-fault="error"]').classList.contains("on"));
  await page.click("#btn-new");
  await ask(page, "BM-24011 주문 지금 어떤 상태예요?");
  check(L("(c) 주문 시스템 오류 → 인계 카드"), (await page.locator("#chat-log .handoff-card").count()) === 1 && (await page.textContent("#chat-log .handoff-card")).includes("주문 시스템 오류"));
  check(L("(c) 인계 뒤 대기 배너, 입력은 가능"), (await page.textContent("#chat-banner")).includes("상담원이 대화를 이어받았습니다") && !(await page.isDisabled("#input")));
  check(L("(c) 실행 과정에 도구 실패 표시"), (await page.textContent("#trace")).includes("시스템 오류"));
  await page.locator("#chat-log .handoff-card summary").click();
  await shot(page, "06-chat-fault-handoff");
  await page.click('#fault-seg [data-fault="none"]');
  await page.waitForFunction(() => document.querySelector('#fault-seg [data-fault="none"]').classList.contains("on"));

  // 5. 고객이 상담원 연결 → 상담원 화면에서 맡고 답하고 종료
  await page.click('#cust-row [data-customer="C-1002"]');
  await page.waitForSelector("#greeting");
  await page.click("#btn-handoff");
  await page.waitForSelector("#chat-log .handoff-card");
  const convId = await page.evaluate(() => sessionStorage.getItem("bd.conv.C-1002"));
  check(L("(d) 상담원 연결 버튼 → 인계 카드"), Boolean(convId));

  const desk = await context.newPage();
  watch(desk, "desk");
  await desk.goto(`${BASE}/desk`);
  await desk.waitForSelector("#rail .ticket");
  const hid = await desk.evaluate(async (cid) => {
    const { handoffs } = await (await fetch("/v1/handoffs")).json();
    return handoffs.find((h) => h.conversation_id === cid).id;
  }, convId);
  await desk.click(`#rail .ticket[data-handoff="${hid}"]`);
  await desk.waitForFunction((id) => document.querySelector(`#rail .ticket[data-handoff="${id}"]`)?.classList.contains("on") && document.querySelector("#summary-card"), hid);
  check(L("(d) 상담원 화면: 요약·대화·근거·도구·초안 구역"), (await desk.locator("#summary-card").count()) === 1 && (await desk.locator("#timeline .msg").count()) >= 2 && (await desk.locator("#evidence-box").count()) === 1 && (await desk.locator("#tool-box").count()) === 1 && (await desk.locator("#draft-box").count()) === 1);
  await shot(desk, "07-desk-waiting");
  await desk.fill("#agent-name", "민지");
  await desk.dispatchEvent("#agent-name", "change");
  await desk.click("#btn-claim");
  await desk.waitForFunction(() => !document.querySelector("#btn-claim"));
  check(L("(d) 맡기 → 진행 상태"), (await desk.textContent("#summary-card")).includes("진행 중"));
  await desk.fill("#reply-text", "안녕하세요, 상담원 민지입니다. 넘겨받은 내용 확인했습니다.");
  await desk.click("#btn-reply");
  await desk.waitForFunction(() => document.querySelectorAll("#timeline .msg.agent").length === 1);
  await page.bringToFront();
  await page.waitForSelector("#chat-log .msg.agent", { timeout: 8000 });
  check(L("(d) 상담원 답변이 고객 화면에 도착"), (await page.textContent("#chat-log .msg.agent")).includes("상담원 민지입니다"));
  await page.fill("#input", "네, 기다릴게요.");
  await page.click("#btn-send");
  await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg.customer:not(.pending)").length >= 2 && document.querySelector("#chat-status").classList.contains("hidden"));
  check(L("(d) 인계 중 고객 메시지에는 AI가 답하지 않음"), (await page.locator("#chat-log .msg.ai:not(#greeting)").count()) === 0);
  await shot(page, "08-chat-agent-reply");
  await desk.bringToFront();
  await desk.waitForFunction(() => document.querySelectorAll("#timeline .msg.customer").length >= 2, null, { timeout: 8000 });
  check(L("(d) 고객의 추가 메시지가 상담원 화면에 도착"), true);
  await shot(desk, "09-desk-assigned");
  await noHScroll(desk, L("상담원 화면"));
  await smallTargets(desk, L("상담원 화면"));
  await desk.fill("#resolve-note", "안내 완료");
  await desk.click("#btn-close");
  await desk.waitForFunction(() => !document.querySelector("#reply-box"));
  check(L("(d) 종료 처리"), (await desk.textContent("#detail")).includes("종료된 건입니다"));
  await shot(desk, "10-desk-resolved");
  // AI가 넘긴 건(주문 시스템 오류): 근거·도구 호출이 채워진 모습
  await desk.click('#tabs [data-filter="all"]');
  const failedId = await desk.evaluate(async () => {
    const { handoffs } = await (await fetch("/v1/handoffs")).json();
    return handoffs.find((h) => h.reason === "tool_failure").id;
  });
  await desk.click(`#rail .ticket[data-handoff="${failedId}"]`);
  await desk.waitForFunction(() => document.querySelector("#tool-box table"));
  check(L("(d) AI가 넘긴 건: 도구 호출 결과와 근거 목록 표시"), (await desk.textContent("#tool-box")).includes("시스템 오류") && (await desk.locator("#evidence-box .ev-item").count()) >= 1 && (await desk.textContent("#summary-card")).includes("AI가 판단해 넘김"));
  await shot(desk, "10b-desk-tool-failure");
  await noHScroll(desk, L("상담원 화면(도구 실패 건)"));
  await page.bringToFront();
  await page.waitForSelector("#btn-new-inline", { timeout: 8000 });
  check(L("(g) 종료된 대화: 입력 막힘 + 새 대화 버튼"), (await page.isDisabled("#input")) && (await page.textContent("#chat-banner")).includes("상담이 종료되었습니다"));
  await shot(page, "11-chat-closed", false);

  // 6. 오류 상태: 글자 수 초과, 네트워크 끊김
  await page.click("#btn-new-inline");
  await page.waitForSelector("#greeting");
  await page.fill("#input", "가".repeat(601));
  await page.click("#btn-send");
  check(L("(g) 600자 초과 안내"), (await page.textContent("#composer-msg")).includes("600자까지"));
  await shot(page, "12-chat-too-long", false);
  await page.fill("#input", "");
  await page.route("**/messages/stream", (route) => route.abort());
  await page.fill("#input", "반품 기한이 어떻게 되나요?");
  await page.click("#btn-send");
  await page.waitForSelector("#chat-log .error-bubble");
  check(L("(g) 네트워크 끊김 → 다시 보내기 버튼"), (await page.locator("#btn-retry").count()) === 1);
  await shot(page, "13-chat-network-error", false);
  await page.unroute("**/messages/stream");
  const aiBefore = await page.locator("#chat-log .msg.ai:not(#greeting)").count();
  await page.click("#btn-retry");
  await page.waitForFunction((n) => document.querySelectorAll("#chat-log .msg.ai:not(#greeting)").length > n, aiBefore, { timeout: 20000 });
  check(L("(g) 다시 보내기로 복구"), (await page.locator("#chat-log .error-bubble").count()) === 0 && (await page.locator("#chat-log .msg.customer").count()) === 1);

  // 7. 운영 화면
  const ops = await context.newPage();
  watch(ops, "ops");
  await ops.goto(`${BASE}/ops`);
  await ops.waitForSelector(".kpi-grid .kpi");
  const kpiText = await ops.textContent(".kpi-grid");
  check(L("(e) 운영 지표가 비어 있지 않음"), /해결률/.test(kpiText) && !/집계할 대화 없음/.test(kpiText) && (await ops.locator(".ops-grid .bar-row").count()) >= 5, kpiText.replace(/\s+/g, " ").slice(0, 120));
  check(L("(e) 해결률 정의가 화면에 보임"), kpiText.includes("분모에서 뺍니다"));
  await shot(ops, "14-ops-overview");
  await noHScroll(ops, L("운영 화면 개요"));
  await smallTargets(ops, L("운영 화면"));
  await ops.click('#filters [data-source="recorded"]');
  await ops.waitForFunction(() => document.querySelector(".kpi-grid").textContent.includes("집계할 대화 없음"));
  check(L("(e) 저장된 기록 필터: 빈 값은 — 와 '집계할 대화 없음'"), (await ops.textContent(".kpi-a .k-value")).trim() === "—");
  await shot(ops, "15-ops-empty", false);
  await ops.click('#filters [data-source="all"]');
  await ops.click('#tabs [data-tab="review"]');
  await ops.waitForSelector("#flag-list .flag");
  check(L("(e) 검토 대기 목록"), (await ops.locator("#flag-list .flag").count()) >= 1 && (await ops.textContent("#flag-list")).includes("주문 시스템 호출 실패"));
  await shot(ops, "16-ops-review");
  await ops.click('#tabs [data-tab="prompts"]');
  await ops.waitForSelector("#prompt-list .prompt-item");
  await ops.click(`#prompt-list [data-prompt="${ACTIVE_AGENT}"]`);
  await ops.waitForSelector("#prompt-list pre.code");
  check(L("(e) 프롬프트 버전 목록과 본문 보기"), (await ops.locator("#prompt-list .prompt-item").count()) >= 3 && (await ops.textContent("#prompt-list pre.code")).includes("바통마켓"));
  // 저장소에 실제 모델 평가 결과(eval_results/index.json)가 있으면 표로, 없으면 없다는 안내로 보인다.
  const evalTables = await ops.locator("#eval-section table.data").count();
  const evalText = await ops.textContent("#eval-section");
  check(L("(e) 평가 결과: 표 또는 없음 안내"), evalTables >= 1 ? evalText.includes("통과율") && evalText.includes("개발용") : evalText.length > 20);
  await shot(ops, "17-ops-prompts");
  await noHScroll(ops, L("운영 화면 프롬프트"));
  await ops.click('#tabs [data-tab="logs"]');
  await ops.waitForSelector("#log-list .log-row");
  check(L("(e) 로그 목록"), (await ops.locator("#log-list .log-row").count()) >= 5);
  await ops.selectOption("#log-type", "handoff.created");
  await ops.waitForFunction(() => [...document.querySelectorAll("#log-list .l-type")].every((n) => n.textContent === "handoff.created"));
  await shot(ops, "18-ops-logs");
  await noHScroll(ops, L("운영 화면 로그"));

  // 검토 대기 → 읽기 전용 대화
  const flaggedId = await ops.evaluate(async () => (await (await fetch("/v1/metrics/summary")).json()).flagged[0].conversation_id);
  const ro = await context.newPage();
  watch(ro, "readonly");
  await ro.goto(`${BASE}/chat?conversation=${flaggedId}`);
  await ro.waitForSelector("#chat-log .handoff-card");
  check(L("읽기 전용 보기: 입력 영역 숨김 + 실행 과정 표시"), (await ro.locator("#composer").isHidden()) && (await ro.locator("#btn-handoff").isHidden()) && (await ro.textContent("#chat-banner")).includes("읽기 전용") && (await ro.locator("#pane-trace .step").first().isVisible()));
  await shot(ro, "19-chat-readonly");

  // 7b. 사람 검수 화면: 정답 없이 문항을 보여 주고, 고른 답을 브라우저에만 저장했다가 글자로 내보낸다
  const rv = await context.newPage();
  watch(rv, "review");
  await rv.goto(`${BASE}/review`);
  await rv.waitForSelector(".review-suite");
  await rv.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("bd.review.")).forEach((k) => localStorage.removeItem(k)));
  await rv.reload();
  await rv.waitForSelector(".review-suite");
  check(L("검수: 묶음 세 개와 건수"), (await rv.locator("button.review-suite").count()) === 3 && (await rv.textContent(".review-suites")).includes("0 / 30"));
  await shot(rv, "50-review-intro");
  await rv.fill("#reviewer", "점검용");
  await rv.click('button.review-suite[data-suite="grounding"]');
  await rv.waitForSelector("#choices");
  const itemText = await rv.textContent("#review-root");
  check(L("검수: 근거·대화·답변이 보이고 정답은 보이지 않음"), itemText.includes("판정할 답변") && itemText.includes("근거") && !/G-T-\d{3}|ungrounded|grounded/.test(itemText));
  await noHScroll(rv, L("검수 문항"));
  await smallTargets(rv, L("검수 문항"));
  await shot(rv, "51-review-grounding");
  await rv.click('#choices [data-value="grounded"]');
  await rv.waitForFunction(() => document.querySelector("#progress").textContent.includes("2 / 30"));
  await rv.reload();
  await rv.waitForSelector("#progress");
  check(L("검수: 새로 고쳐도 이어서 진행"), (await rv.textContent("#progress")).includes("2 / 30"));
  await rv.click("#btn-home");
  await rv.click('button.review-suite[data-suite="resolution"]');
  await rv.waitForSelector("#choices");
  check(L("검수: 해결 판정은 다섯 가지 중 하나"), (await rv.locator("#choices .review-choice").count()) === 5);
  await rv.click("#btn-home");
  await rv.click('button.review-suite[data-suite="agent"]');
  await rv.waitForSelector("#choices");
  check(L("검수: 에이전트 응대에는 기준과 응대 기록이 함께 보임"), (await rv.textContent("#review-root")).includes("기대한 행동") && (await rv.locator(".review-line").count()) >= 2);
  await shot(rv, "52-review-agent");
  await rv.click("#btn-home");
  await rv.click("#btn-result");
  await rv.waitForSelector("#export");
  const exported = JSON.parse(await rv.inputValue("#export"));
  check(L("검수: 결과에 표본 번호·검수자·고른 답이 들어 있음"), exported.kit.length === 12 && exported.reviewer === "점검용" && Object.keys(exported.labels).length === 1 && Object.values(exported.labels)[0].label === "grounded");
  await noHScroll(rv, L("검수 결과"));
  await shot(rv, "53-review-result");
  await rv.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith("bd.review.")).forEach((k) => localStorage.removeItem(k)));
  await rv.close();

  // 8. 임베드
  const emb = await context.newPage();
  watch(emb, "embed");
  await emb.goto(`${BASE}/embed-demo`);
  await emb.waitForSelector(".bd-launcher");
  await emb.waitForFunction(() => document.querySelector("#order-status").textContent !== "—");
  await shot(emb, "20-embed-closed", false);
  await emb.click(".bd-launcher");
  const frame = emb.frameLocator(".bd-frame");
  await frame.locator("#greeting").waitFor({ timeout: 10000 });
  check(L("(f) 임베드: 버튼을 누르면 위젯 안에 채팅이 열림"), (await frame.locator("#input").count()) === 1 && (await frame.locator(".demo-col").isHidden()) && (await frame.locator(".notice").isVisible()));
  await frame.locator("#input").fill("BM-24011 주문 지금 어떤 상태예요?");
  await frame.locator("#btn-send").click();
  await frame.locator("#chat-log .msg.ai:not(#greeting) .bubble").waitFor({ timeout: 20000 });
  check(L("(f) 임베드: 위젯 안에서 문의·답변"), (await frame.locator("#chat-log .source-chip").count()) >= 1);
  await shot(emb, "21-embed-open", false);
  await noHScroll(emb, L("임베드 예시"));
  await emb.click(".bd-close");
  check(L("(f) 임베드: 닫기"), await emb.locator(".bd-panel").isHidden());

  check(L("콘솔 오류 없음"), errors.length === 0, errors.slice(0, 5).join(" || "));
  await browser.close();
}

(async () => {
  // 서비스가 쓰는 프롬프트 버전은 서버에 묻는다(버전을 올려도 이 점검을 고치지 않게).
  ACTIVE_AGENT = (await (await fetch(`${BASE}/v1/release`)).json()).prompts.agent.id;
  for (const [name, viewport] of Object.entries(VIEWPORTS)) {
    if (only && only !== name) continue;
    try {
      await run(name, viewport);
    } catch (error) {
      check(`[${name}] 실행 중단`, false, String(error).slice(0, 400));
    }
  }
  const failed = results.filter((r) => !r.ok);
  console.log(`\n합계 ${results.length}건 중 통과 ${results.length - failed.length}건, 실패 ${failed.length}건`);
  process.exit(failed.length ? 1 : 0);
})();
