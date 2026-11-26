"""공개 데모가 "이 커밋"을 서빙하는지 기다렸다가, 실제로 응답하는지 확인한다.

push 직후 몇 분은 이전 배포본이 답하고, 잠든 무료 인스턴스는 깨는 데 시간이 걸린다. 그때 검사하면
옛 빌드를 검사하고 새 빌드라고 적게 된다. 그래서 GET /v1/release 의 git_sha 가 기대한 커밋과 같아질
때까지 기다린 뒤에만 검사를 시작한다.

    python scripts/live_check.py https://<주소> --sha <커밋> [--with-model] [--out result.json]

--with-model 은 실제 모델로 문의 한 건을 보낸다(서버의 모델 호출 한도에서 차감된다).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any


def call(base: str, path: str, body: dict[str, Any] | None = None, timeout: float = 60.0) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(base + path, data=data, headers={"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, raw.decode("utf-8", "replace")


def wait_for_sha(base: str, sha: str, timeout_s: int) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    last = "응답 없음"
    while time.time() < deadline:
        try:
            status, body = call(base, "/v1/release", timeout=30)
            if status == 200 and isinstance(body, dict):
                last = body.get("git_sha", "?")
                if last == sha:
                    return body
        except OSError as exc:
            last = f"{type(exc).__name__}"
        print(f"  기다리는 중… 지금 배포본: {str(last)[:12]} / 기대: {sha[:12]}", flush=True)
        time.sleep(20)
    raise SystemExit(f"{timeout_s}초 안에 {sha[:12]} 이 배포되지 않았습니다(마지막: {last}).")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--sha", default=None)
    parser.add_argument("--timeout", type=int, default=1500)
    parser.add_argument("--with-model", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    base = args.base.rstrip("/")
    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    release = wait_for_sha(base, args.sha, args.timeout) if args.sha else call(base, "/v1/release")[1]
    check("배포된 커밋", not args.sha or release["git_sha"] == args.sha, release["git_sha"])
    check("엔진 표시", release["engine"]["engine"] in ("live", "off"), release["engine"]["label"])
    check("지식 문서", release["knowledge_base"]["documents"] >= 20, release["knowledge_base"])
    check("프롬프트 버전", bool(release["prompts"]["agent"]["id"]), release["prompts"]["agent"])
    for page in ("/", "/chat", "/desk", "/ops", "/embed-demo", "/docs", "/example-commerce/docs", "/static/widget.js"):
        status, _ = call(base, page)
        check(f"화면 {page}", status == 200, status)
    status, search = call(base, "/v1/kb/search", {"query": "단순변심 반품 배송비", "k": 3})
    check(
        "지식 검색",
        status == 200 and search["hits"][0]["doc_id"] == "return-policy",
        search["hits"][0]["chunk_id"] if status == 200 else status,
    )
    status, order = call(base, "/v1/demo/customers")
    check("예시 고객·주문", status == 200 and len(order["customers"]) == 3, status)
    status, recorded = call(base, "/v1/conversations?source=recorded")
    check("저장된 상담 기록 목록", status == 200, f"{len(recorded.get('conversations', []))}건" if status == 200 else status)

    if args.with_model and release["engine"]["engine"] == "live":
        status, conversation = call(base, "/v1/conversations", {"customer_id": "C-1001", "channel": "api"})
        check("대화 시작", status == 200, status)
        if status == 200:
            status, turn = call(
                base,
                f"/v1/conversations/{conversation['id']}/messages",
                {"text": "단순변심 반품은 며칠 안에 신청해야 하나요?"},
                timeout=120,
            )
            ok = status == 200 and not turn["standby"] and "7일" in turn["message"]["text"].replace(" ", "")
            detail = (
                {
                    "status": status,
                    "text": turn["message"]["text"][:120],
                    "citations": [c["id"] for c in turn["message"]["payload"]["citations"]],
                    "turn": turn["turn"],
                }
                if status == 200
                else turn
            )
            check(
                "실제 모델 응답(반품 기한 7일, 출처 포함)",
                ok and bool(turn["message"]["payload"]["citations"]) if status == 200 else False,
                detail,
            )
    elif args.with_model:
        check("실제 모델 응답", False, "서버에 모델 키가 없어 모델 호출이 꺼져 있습니다")

    result = {"base": base, "release": release, "checks": checks, "passed": all(c["ok"] for c in checks)}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
