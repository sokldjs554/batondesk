// 모델 호출이 꺼진 서버(공개 데모가 키 없이 뜬 경우)에서의 화면 상태 점검.
const { chromium } = require("playwright");
const path = require("path");
const BASE = process.env.BASE || "http://localhost:8812";
const OUT = path.resolve(process.env.SHOTS_DIR || path.join(__dirname, "..", "..", "var", "ui-shots"));
require("fs").mkdirSync(OUT, { recursive: true });
const SCALE = Number(process.env.SCALE || 1);
(async () => {
  let failed = 0;
  const check = (name, ok, detail = "") => { if (!ok) failed++; console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`); };
  for (const [name, viewport] of Object.entries({ desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } })) {
    const browser = await chromium.launch();
    const context = await browser.newContext({ viewport, deviceScaleFactor: SCALE, locale: "ko-KR" });
    const errors = [];
    const page = await context.newPage();
    page.on("console", (m) => m.type() === "error" && !m.location().url.includes("/v1/evals") && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`${BASE}/chat`);
    await page.waitForSelector("#greeting");
    await page.waitForFunction(() => document.querySelector("#engine-slot").textContent.includes("모델 호출 꺼짐"));
    check(`[off/${name}] 엔진 배지: 모델 호출 꺼짐 · 저장된 기록만`, (await page.textContent("#engine-slot")).includes("모델 호출 꺼짐 · 저장된 기록만"));
    check(`[off/${name}] 채팅: 입력 막힘 + 안내 배너`, (await page.isDisabled("#input")) && (await page.isDisabled("#btn-handoff")) && (await page.textContent("#chat-banner")).includes("모델 호출이 꺼져"));
    await page.click('#demo-tabs [data-pane="recordings"]');
    check(`[off/${name}] 저장된 기록이 없으면 없다고 표시`, (await page.textContent("#rec-list")).includes("저장된 상담 기록이 아직 없습니다"));
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(OUT, `${name}-30-off-chat.png`), fullPage: true });
    await page.goto(`${BASE}/desk`);
    await page.waitForSelector("#detail .card");
    check(`[off/${name}] 상담원 화면 빈 상태: 인계 만드는 방법 안내`, (await page.textContent("#detail")).includes("인계 건을 만들어 보려면"));
    await page.screenshot({ path: path.join(OUT, `${name}-31-off-desk.png`), fullPage: true });
    await page.goto(`${BASE}/ops`);
    await page.waitForSelector(".kpi-grid .kpi");
    const text = await page.textContent("#tab-overview");
    check(`[off/${name}] 운영 화면 빈 상태: — 와 '집계할 대화 없음'`, (await page.textContent(".kpi-a .k-value")).trim() === "—" && text.includes("집계할 대화 없음") && text.includes("이 조건에 해당하는 대화가 아직 없습니다"));
    const sw = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
    check(`[off/${name}] 가로 스크롤 없음`, sw[0] <= sw[1] + 1);
    await page.screenshot({ path: path.join(OUT, `${name}-32-off-ops.png`), fullPage: true });
    check(`[off/${name}] 콘솔 오류 없음`, errors.length === 0, errors.join(" || "));
    await browser.close();
  }
  process.exit(failed ? 1 : 0);
})();
