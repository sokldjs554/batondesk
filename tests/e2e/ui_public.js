// 배포된 공개 데모를 실제 브라우저로 본다(PC 1440×900, 모바일 390×844).
// 로컬 점검(ui_flows.js 등)과 달리 서버를 우리가 띄우지 않는다. 주소만 받아서, 처음 온 사람이 보는 화면이 뜨는지 확인한다.
//   BASE=https://<공개 주소> node tests/e2e/ui_public.js
//   WITH_MODEL=1 을 주면 PC 화면에서 실제 모델에 문의 한 건을 보낸다(서버의 호출 한도에서 차감된다).
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");
const BASE = (process.env.BASE || "").replace(/\/$/, "");
const OUT = path.resolve(process.env.SHOTS_DIR || path.join(__dirname, "..", "..", "var", "public-shots"));
const SCALE = Number(process.env.SCALE || 1);
const WITH_MODEL = process.env.WITH_MODEL === "1";
if (!BASE) {
  console.error("BASE 환경 변수에 공개 주소를 넣어야 합니다.");
  process.exit(2);
}
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const results = [];
  const check = (name, ok, detail = "") => {
    results.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 300) });
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + String(detail).slice(0, 300) : ""}`);
  };
  const release = await (await fetch(`${BASE}/v1/release`)).json();
  const engine = release.engine.engine;
  const recorded = (await (await fetch(`${BASE}/v1/conversations?source=recorded&limit=50`)).json()).conversations;
  console.log(`배포본: 커밋 ${String(release.git_sha).slice(0, 8)} · 엔진 ${engine} (${release.engine.label}) · 프롬프트 ${release.prompts.agent.id} · 저장 기록 ${recorded.length}건`);

  for (const [name, viewport] of Object.entries({ desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } })) {
    const L = (text) => `[public/${name}] ${text}`;
    const browser = await chromium.launch();
    const context = await browser.newContext({ viewport, deviceScaleFactor: SCALE, locale: "ko-KR" });
    const errors = [];
    const page = await context.newPage();
    page.setDefaultTimeout(60000);
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    const shot = (file, fullPage = true) => page.screenshot({ path: path.join(OUT, `${name}-${file}.png`), fullPage });
    const noHScroll = async (label) => {
      const sw = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
      check(L(`${label}: 가로 스크롤 없음`), sw[0] <= sw[1] + 1, `${sw[0]} / ${sw[1]}`);
    };

    // 1. 첫 화면
    await page.goto(`${BASE}/`);
    await page.waitForSelector(".hero h1");
    await page.waitForFunction(() => !document.querySelector("#engine-slot").textContent.includes("확인 중"));
    const badge = (await page.textContent("#engine-slot")).trim();
    check(L("첫 화면: 문제 설명과 사용 순서가 첫 화면 안에 있음"), await page.evaluate(() => document.querySelector(".hero h1").getBoundingClientRect().top < window.innerHeight && document.querySelectorAll(".try-steps li").length === 3));
    check(L("첫 화면: 엔진 배지가 서버 상태와 같음"), engine === "live" ? badge.includes("실제 모델 호출") : badge.includes("모델 호출 꺼짐"), badge);
    check(L("첫 화면: 예시 데이터 고지"), (await page.textContent("body")).includes("예시 데이터"));
    check(L("첫 화면: 바닥글의 커밋이 배포본과 같음"), (await page.textContent("#footer-sha")).trim() === String(release.git_sha).slice(0, 7), await page.textContent("#footer-sha"));
    await noHScroll("첫 화면");
    await shot("01-landing-fold", false);
    await shot("01-landing");

    // 2. 고객 화면: 저장된 기록
    await page.goto(`${BASE}/chat`);
    await page.waitForSelector("#greeting");
    await page.click('#demo-tabs [data-pane="recordings"]');
    await page.waitForSelector("#rec-list li");
    check(L("고객 화면: 실제 모델로 만든 저장 기록 목록"), (await page.locator("#rec-list li a").count()) === recorded.length && recorded.length >= 1, `${recorded.length}건`);
    await shot("02-chat-recordings", false);
    if (recorded.length) {
      const target = recorded.find((c) => !c.handoff) || recorded[0];
      await page.goto(`${BASE}/chat?conversation=${target.id}`);
      await page.waitForSelector("#chat-log .msg.ai");
      const banner = await page.textContent("#chat-banner");
      check(L("기록 보기: 읽기 전용 + 출처(모델·프롬프트·커밋)"), banner.includes("읽기 전용") && /모델 claude-/.test(banner) && /프롬프트 agent-v\d+/.test(banner) && /커밋 [0-9a-f]{7}/.test(banner), banner.slice(0, 200));
      await noHScroll("기록 보기");
      await shot("03-chat-recording");
    }

    // 3. 실제 모델에 문의 한 건 (PC 에서만, 요청했을 때만)
    if (WITH_MODEL && name === "desktop") {
      if (engine !== "live") {
        check(L("실제 모델 문의"), false, "서버에 모델 키가 없어 모델 호출이 꺼져 있습니다");
      } else {
        await page.goto(`${BASE}/chat?customer=C-1001`);
        await page.waitForSelector("#greeting");
        await page.evaluate(() => sessionStorage.clear());
        await page.reload();
        await page.waitForSelector("#greeting");
        const started = Date.now();
        await page.fill("#input", "무료배송으로 받은 상품을 단순변심으로 반품하면 배송비가 얼마인가요?");
        await page.click("#btn-send");
        await page.waitForFunction(() => document.querySelectorAll("#chat-log .msg.ai:not(#greeting), #chat-log .handoff-card").length > 0 && document.querySelector("#chat-status").classList.contains("hidden"), null, { timeout: 120000 });
        const seconds = ((Date.now() - started) / 1000).toFixed(1);
        const answer = (await page.locator("#chat-log .msg.ai:not(#greeting)").first().textContent()) || "";
        check(L("실제 모델 문의: 답변에 6,000원과 출처"), answer.replace(/[\s,]/g, "").includes("6000원") && (await page.locator("#chat-log .msg.ai:not(#greeting) .source-chip").count()) >= 1, `${seconds}초 · ${answer.replace(/\s+/g, " ").slice(0, 160)}`);
        check(L("실제 모델 문의: 대화가 '실제 모델 호출'로 표시됨"), (await page.textContent("#chat-sub")).includes("실제 모델 호출") || (await page.textContent("#chat-log")).includes("실제 모델 호출"));
        await page.click('#demo-tabs [data-pane="trace"]');
        await page.waitForSelector("#pane-trace .step");
        check(L("실제 모델 문의: 응답 과정에 검색·모델 호출·근거 검사"), (await page.locator("#pane-trace .step").count()) >= 3);
        await shot("04-chat-live-answer");
      }
    }

    // 4. 상담원 화면, 운영 화면
    await page.goto(`${BASE}/desk`);
    await page.waitForSelector("#detail .desk-detail, #detail .card, #detail .empty");
    check(L("상담원 화면이 뜸"), (await page.textContent("h1")).includes("AI가 넘긴 대화"));
    await noHScroll("상담원 화면");
    await shot("05-desk");
    await page.goto(`${BASE}/ops`);
    await page.waitForSelector(".kpi-grid .kpi");
    check(L("운영 화면: 지표 카드"), (await page.locator(".kpi-grid .kpi").count()) >= 4);
    await noHScroll("운영 화면");
    await shot("06-ops");
    await page.click('#tabs [data-tab="prompts"]');
    await page.waitForSelector("#prompt-list .prompt-item");
    await page.waitForSelector("#eval-section");
    const evalText = await page.textContent("#eval-section");
    check(L("운영 화면: 프롬프트 버전 목록과 평가 결과 표"), (await page.locator("#prompt-list .prompt-item").count()) >= 3 && (await page.locator("#eval-section table.data").count()) >= 1 && evalText.includes("평가용"));
    await page.locator("#eval-section").scrollIntoViewIfNeeded();
    await shot("07-ops-evals", false);

    check(L("콘솔 오류 없음"), errors.length === 0, errors.slice(0, 4).join(" || "));
    await browser.close();
  }

  const summary = { base: BASE, checked_at: new Date().toISOString(), release, recordings: recorded.length, with_model: WITH_MODEL, checks: results, passed: results.every((r) => r.ok) };
  fs.writeFileSync(path.join(OUT, "ui-public.json"), JSON.stringify(summary, null, 2));
  process.exit(summary.passed ? 0 : 1);
})().catch((error) => {
  console.log(`FAIL  실행 중단  — ${String(error).slice(0, 400)}`);
  process.exit(1);
});
