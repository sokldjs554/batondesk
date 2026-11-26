"""docs/api.md 의 엔드포인트 표를 앱의 OpenAPI 스키마에서 만들어 낸다.

문서의 표가 코드와 어긋나지 않게 하려는 것이다. `--check` 는 파일이 최신인지 확인만 한다(CI용).

    python scripts/gen_api_doc.py           # docs/api.md 갱신
    python scripts/gen_api_doc.py --check
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from batondesk.api.app import create_app
from batondesk.settings import REPO_ROOT, Settings
from batondesk.store.db import Database

BEGIN, END = "<!-- endpoints:begin -->", "<!-- endpoints:end -->"
TARGET = REPO_ROOT / "docs" / "api.md"


def render() -> str:
    settings = Settings(llm_mode="off", _env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings, db=Database(":memory:"))) as client:
        main = client.get("/openapi.json").json()
        commerce = client.get("/example-commerce/openapi.json").json()

    def table(schema: dict, prefix: str = "") -> list[str]:
        groups: dict[str, list[str]] = {}
        for path, methods in schema["paths"].items():
            for method, spec in methods.items():
                tag = (spec.get("tags") or ["기타"])[0]
                groups.setdefault(tag, []).append(f"| `{method.upper()}` | `{prefix}{path}` | {spec.get('summary', '')} |")
        lines: list[str] = []
        for tag, rows in groups.items():
            lines += [f"**{tag}**", "", "| 메서드 | 경로 | 설명 |", "|---|---|---|", *rows, ""]
        return lines

    lines = [BEGIN, "", "### 서비스 API", "", *table(main), "### 예시 주문 시스템 API (`/example-commerce`)", ""]
    lines += [
        "가상의 예시 시스템입니다. 에이전트의 도구가 호출하며, 실제 주문·결제와 연결되어 있지 않습니다.",
        "",
        *table(commerce, "/example-commerce"),
        END,
    ]
    return "\n".join(lines)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    start, end = text.index(BEGIN), text.index(END) + len(END)
    updated = text[:start] + render() + text[end:]
    if "--check" in sys.argv:
        if updated != text:
            print("docs/api.md 의 엔드포인트 표가 코드와 다릅니다. python scripts/gen_api_doc.py 를 실행하세요.", file=sys.stderr)
            return 1
        print("docs/api.md 의 엔드포인트 표가 코드와 같습니다.")
        return 0
    Path(TARGET).write_text(updated, encoding="utf-8")
    print("docs/api.md 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
