"""명령줄 진입점: 서버 실행, 프롬프트 잠금 검사."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(prog="batondesk")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="API·웹 서버 실행")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    prompts = sub.add_parser("prompts", help="프롬프트 버전 검사")
    prompts.add_argument("action", choices=["check", "lock", "list"])
    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("batondesk.api.app:app_factory", factory=True, host=args.host, port=args.port)
        return

    from batondesk.prompts.registry import PromptRegistry
    from batondesk.settings import get_settings

    registry = PromptRegistry(get_settings().prompts_dir)
    if args.action == "list":
        for version in registry.all_versions():
            active = "*" if registry.active[version.kind] == version.id else " "
            print(f"{active} {version.id:16s} {version.sha256[:12]}  {version.summary}")
    elif args.action == "lock":
        lock = registry.write_lock()
        print(f"잠금 파일을 갱신했습니다: {len(lock)}개 버전")
    else:
        problems = registry.check_lock()
        for problem in problems:
            print(f"오류: {problem}", file=sys.stderr)
        if problems:
            sys.exit(1)
        print(f"프롬프트 {len(registry.versions)}개 버전 확인. 잠긴 버전의 내용은 바뀌지 않았습니다.")


if __name__ == "__main__":
    main()
