/* BatonDesk 임베드 위젯.
 *
 * 쓰는 법: 페이지 아무 곳에나 아래 한 줄을 넣는다.
 *   <script src="https://<BatonDesk 서버>/static/widget.js" data-customer="C-1001"></script>
 *
 * - data-customer: 이 페이지가 로그인으로 확인한 고객 ID(데모에서는 예시 고객 ID).
 * - data-label:    버튼 글자(기본 "상담하기").
 * - data-origin:   BatonDesk 서버 주소. 생략하면 이 스크립트를 내려받은 주소를 쓴다.
 *
 * 채팅 화면은 iframe 안에서 돌기 때문에 넣는 쪽 페이지의 스타일·스크립트와 서로 영향을 주지 않는다.
 */
(function () {
  "use strict";
  var script = document.currentScript;
  if (!script || window.__batondeskWidget) return;
  window.__batondeskWidget = true;

  var origin = script.dataset.origin || new URL(script.src, location.href).origin;
  var customer = script.dataset.customer || "";
  var label = script.dataset.label || "상담하기";
  var src = origin + "/chat?embed=1" + (customer ? "&customer=" + encodeURIComponent(customer) : "");

  var style = document.createElement("style");
  var font = "'Pretendard Variable',Pretendard,-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif";
  style.textContent = [
    ".bd-launcher{position:fixed;right:20px;bottom:20px;z-index:2147483000;display:inline-flex;align-items:center;gap:10px;min-height:52px;padding:0 20px 0 14px;border-radius:999px;border:1px solid #1b1a44;background:#1b1a44;color:#f4f1ff;font:600 16px/1 " + font + ";box-shadow:0 8px 24px rgba(27,26,68,.28);cursor:pointer}",
    ".bd-launcher:hover{background:#2e2d6b}",
    ".bd-launcher:focus-visible,.bd-close:focus-visible{outline:2px solid #3a3fd8;outline-offset:2px}",
    ".bd-launcher i{display:inline-block;width:26px;height:26px;border-radius:8px;background:#f4f1ff;position:relative}",
    ".bd-launcher i::before,.bd-launcher i::after{content:'';position:absolute;top:10px;height:6px;border-radius:3px}",
    ".bd-launcher i::before{left:4px;width:10px;background:#ffd45e}",
    ".bd-launcher i::after{right:4px;width:6px;background:#ff6b4a}",
    ".bd-panel{position:fixed;right:20px;bottom:84px;z-index:2147483001;width:400px;max-width:calc(100vw - 24px);height:min(680px,calc(100vh - 110px));display:none;flex-direction:column;border-radius:16px;border:1px solid rgba(27,26,68,.24);background:#ffffff;box-shadow:0 12px 36px rgba(27,26,68,.24);overflow:hidden}",
    ".bd-panel.bd-open{display:flex}",
    ".bd-bar{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:2px 6px 2px 16px;background:#1b1a44;color:#f4f1ff;font:600 15px/1.2 " + font + "}",
    ".bd-close{min-width:44px;min-height:44px;border-radius:8px;border:0;background:transparent;color:#f4f1ff;font:500 14px/1 " + font + ";cursor:pointer}",
    ".bd-close:hover{background:rgba(252,245,232,.12)}",
    ".bd-frame{flex:1;width:100%;border:0;background:#ffffff}",
    "@media (max-width:560px){.bd-panel{inset:0;right:0;bottom:0;width:100%;max-width:none;height:100%;border-radius:0;border:0;box-shadow:none}.bd-launcher{right:14px;bottom:14px}}",
    "@media (prefers-reduced-motion:reduce){.bd-launcher{transition:none}}",
  ].join("");
  document.head.appendChild(style);

  var launcher = document.createElement("button");
  launcher.type = "button";
  launcher.className = "bd-launcher";
  launcher.setAttribute("aria-haspopup", "dialog");
  launcher.setAttribute("aria-expanded", "false");
  launcher.appendChild(document.createElement("i"));
  launcher.appendChild(document.createTextNode(label));

  var panel = document.createElement("div");
  panel.className = "bd-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "상담 채팅");
  var bar = document.createElement("div");
  bar.className = "bd-bar";
  var title = document.createElement("span");
  title.textContent = "AI 상담";
  var close = document.createElement("button");
  close.type = "button";
  close.className = "bd-close";
  close.textContent = "닫기";
  bar.appendChild(title);
  bar.appendChild(close);
  panel.appendChild(bar);

  var frame = null;
  function setOpen(open) {
    if (open && !frame) {
      frame = document.createElement("iframe");
      frame.className = "bd-frame";
      frame.title = "상담 채팅";
      frame.src = src;
      panel.appendChild(frame);
    }
    panel.classList.toggle("bd-open", open);
    launcher.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) close.focus();
  }
  launcher.addEventListener("click", function () {
    setOpen(!panel.classList.contains("bd-open"));
  });
  close.addEventListener("click", function () {
    setOpen(false);
    launcher.focus();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && panel.classList.contains("bd-open")) setOpen(false);
  });

  function mount() {
    document.body.appendChild(panel);
    document.body.appendChild(launcher);
  }
  if (document.body) mount();
  else document.addEventListener("DOMContentLoaded", mount);
})();
