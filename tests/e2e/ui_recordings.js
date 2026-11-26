// 키 없이 뜬 공개 데모가 보여 주는 화면: 모델 호출은 꺼져 있고, 실제 모델로 만들어 저장한 상담 기록(data/recordings)만 읽기 전용으로 본다.
// 기록이 한 건도 없는 저장소(기록을 만들기 전)에서는 건너뛴다.
const { chromium } = require("playwright");
const path = require("path");
const BASE = process.env.BASE || "http://localhost:8813";
const OUT = path.resolve(process.env.SHOTS_DIR || path.join(__dirname, "..", "..", "var", "ui-shots"));
require("fs").mkdirSync(OUT, { recursive: true });
const SCALE = Number(process.env.SCALE || 1);

(async () => {
  const listed = (await (await fetch(`${BASE}/v1/conversations?source=recorded&limit=50`)).json()).conversations;
  if (listed.length === 0) {
    console.log("SKIP  저장된 상담 기록이 없어 기록 화면 점검을 건너뜁니다(data/recordings 가 비어 있음).");
    process.exit(0);
  }
  let failed = 0;
  const check = (name, ok, detail = "") => {
    if (!ok) failed++;
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
  };
  const answered = listed.find((c) => !c.handoff) || listed[0];
  const handed = listed.find((c) => c.handoff);

  for (const [name, viewport] of Object.entries({ desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } })) {
    const L = (text) => `[rec/${name}] ${text}`;
    const browser = await chromium.launch();
    const context = await browser.newContext({ viewport, deviceScaleFactor: SCALE, locale: "ko-KR" });
    const errors = [];
    const page = await context.newPage();
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(e.message));
    const noHScroll = async (label) => {
      const sw = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
      check(L(`${label}: 가로 스크롤 없음`), sw[0] <= sw[1] + 1, `${sw[0]} > ${sw[1]}`);
    };

    // 1. 기록 목록
    await page.goto(`${BASE}/chat`);
    await page.waitForSelector("#greeting");
    await page.waitForFunction(() => document.querySelector("#engine-slot").textContent.includes("모델 호출 꺼짐"));
    check(L("입력은 막혀 있고 저장된 기록을 보라고 안내"), (await page.isDisabled("#input")) && (await page.textContent("#chat-banner")).includes("저장된 상담 기록"));
    await page.click('#demo-tabs [data-pane="recordings"]');
    await page.waitForSelector("#rec-list li a");
    check(L("기록 목록: 건수 표시"), (await page.locator("#rec-list li a").count()) === listed.length && (await page.textContent("#rec-count")).trim() === String(listed.length));
    await page.locator("#pane-recordings").scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(OUT, `${name}-40-rec-list.png`), fullPage: true });

    // 2. 답변으로 끝난 기록: 읽기 전용, 출처(모델·프롬프트·커밋) 표시, 근거와 실행 과정
    await page.goto(`${BASE}/chat?conversation=${answered.id}`);
    await page.waitForSelector("#chat-log .msg.ai");
    const banner = await page.textContent("#chat-banner");
    check(L("기록 보기: 읽기 전용 + 실제 모델 호출 기록이라는 표시"), banner.includes("읽기 전용") && banner.includes("실제 모델 호출 기록"), banner.slice(0, 160));
    check(L("기록 보기: 모델·프롬프트 버전·커밋·실행 로그 링크"), /모델 claude-/.test(banner) && /프롬프트 agent-v\d+/.test(banner) && /커밋 [0-9a-f]{7}/.test(banner) && (await page.locator("#chat-banner a").count()) === 1);
    check(L("기록 보기: 고객 메시지가 정해 둔 글이라는 표시"), banner.includes("고객 메시지"));
    check(L("기록 보기: 입력 영역과 상담원 연결 버튼 숨김"), (await page.locator("#composer").isHidden()) && (await page.locator("#btn-handoff").isHidden()));
    check(L("기록 보기: 답변에 근거 표시가 달려 있음"), (await page.locator("#chat-log .msg.ai .cite, #chat-log .msg.ai .sources, #chat-log .msg.ai [data-evidence]").count()) >= 1);
    await page.click('#demo-tabs [data-pane="trace"]');
    await page.waitForSelector("#pane-trace .step");
    const trace = await page.textContent("#pane-trace");
    check(L("기록 보기: 응답 과정에 검색·모델 호출·근거 검사가 남아 있음"), (await page.locator("#pane-trace .step").count()) >= 3 && trace.includes("claude-"), trace.replace(/\s+/g, " ").slice(0, 140));
    await noHScroll("기록 보기");
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: path.join(OUT, `${name}-41-rec-chat.png`), fullPage: true });

    // 3. 상담원에게 넘어간 기록
    if (handed) {
      await page.goto(`${BASE}/chat?conversation=${handed.id}`);
      await page.waitForSelector("#chat-log .handoff-card");
      check(L("인계 기록: 넘긴 이유가 대화에 보임"), (await page.textContent("#chat-log .handoff-card")).length > 10);
      await page.screenshot({ path: path.join(OUT, `${name}-42-rec-handoff.png`), fullPage: true });
      await page.goto(`${BASE}/desk`);
      await page.waitForSelector("#detail .desk-detail");
      const desk = await page.textContent("#detail");
      check(
        L("상담원 화면: 기록의 인계 건을 요약·대화·근거·도구 결과와 함께 볼 수 있음(읽기 전용)"),
        ["AI 인계 요약", "대화 이력", "근거 목록", "인계 직전의 도구 호출", "읽기만 할 수 있습니다"].every((t) => desk.includes(t)),
        desk.replace(/\s+/g, " ").slice(0, 120),
      );
      await noHScroll("상담원 화면");
      await page.screenshot({ path: path.join(OUT, `${name}-43-rec-desk.png`), fullPage: true });
    }

    // 4. 운영 화면: 저장된 기록만 골라 보면 실제 모델의 지연·호출 수가 집계된다
    await page.goto(`${BASE}/ops`);
    await page.waitForSelector(".kpi-grid .kpi");
    await page.click('#filters [data-source="recorded"]');
    await page.waitForFunction(() => !document.querySelector(".kpi-grid").textContent.includes("집계할 대화 없음"));
    const kpi = await page.textContent("#tab-overview");
    check(L("운영 화면: 저장된 기록의 지표가 집계됨"), (await page.textContent(".kpi-a .k-value")).trim() !== "—" && /모델 호출/.test(kpi), kpi.replace(/\s+/g, " ").slice(0, 140));
    await noHScroll("운영 화면");
    await page.screenshot({ path: path.join(OUT, `${name}-44-rec-ops.png`), fullPage: true });

    check(L("콘솔 오류 없음"), errors.length === 0, errors.slice(0, 4).join(" || "));
    await browser.close();
  }
  process.exit(failed ? 1 : 0);
})();
