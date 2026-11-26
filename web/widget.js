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
  style.textContent = [
    ".bd-launcher{position:fixed;right:20px;bottom:20px;z-index:2147483000;display:inline-flex;align-items:center;gap:10px;min-height:56px;padding:0 22px 0 16px;border-radius:999px;border:1.5px solid #1c1b4b;background:#3a3fd8;color:#fff;font:800 16px/1 -apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif;box-shadow:4px 4px 0 #1c1b4b;cursor:pointer}",
    ".bd-launcher:hover{transform:translate(-1px,-1px);box-shadow:5px 5px 0 #1c1b4b}",
    ".bd-launcher i{display:inline-block;width:28px;height:28px;border-radius:9px;background:#ffd667;border:1.5px solid #1c1b4b;position:relative}",
    ".bd-launcher i::after{content:'';position:absolute;left:6px;right:6px;top:10px;height:5px;border-radius:5px;background:#1c1b4b}",
    ".bd-panel{position:fixed;right:20px;bottom:90px;z-index:2147483001;width:400px;max-width:calc(100vw - 24px);height:min(680px,calc(100vh - 120px));display:none;flex-direction:column;border-radius:24px;border:1.5px solid #1c1b4b;background:#fff7ea;box-shadow:6px 6px 0 #1c1b4b;overflow:hidden}",
    ".bd-panel.bd-open{display:flex}",
    ".bd-bar{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 8px 8px 16px;background:#1c1b4b;color:#fff7ea;font:800 15px/1.2 -apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Noto Sans KR','Malgun Gothic',sans-serif}",
    ".bd-close{min-width:44px;min-height:40px;border-radius:12px;border:1.5px solid #fff7ea;background:transparent;color:#fff7ea;font:700 14px/1 inherit;cursor:pointer}",
    ".bd-frame{flex:1;width:100%;border:0;background:#fff7ea}",
    "@media (max-width:560px){.bd-panel{inset:0;right:0;bottom:0;width:100%;max-width:none;height:100%;border-radius:0;border:0;box-shadow:none}.bd-launcher{right:14px;bottom:14px}}",
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
