#!/usr/bin/env bash
# 화면 점검 실행기: 규칙 엔진 서버와 모델 꺼진 서버를 띄우고 브라우저 흐름을 돌린 뒤 정리한다.
#   tests/e2e/run.sh              # 두 화면 크기 모두
#   SCALE=2 SHOTS_DIR=docs/images/ui tests/e2e/run.sh   # 사람이 볼 캡처(2배 해상도)
set -euo pipefail
cd "$(dirname "$0")/../.."
work=$(mktemp -d)
cleanup() { kill "${pid_on:-}" "${pid_off:-}" 2>/dev/null || true; rm -rf "$work"; }
trap cleanup EXIT

BATONDESK_LLM_MODE=scripted BATONDESK_DB_PATH="$work/on.sqlite3" batondesk serve --port 8811 >"$work/on.log" 2>&1 &
pid_on=$!
env -u ANTHROPIC_API_KEY BATONDESK_LLM_MODE=off BATONDESK_DB_PATH="$work/off.sqlite3" batondesk serve --port 8812 >"$work/off.log" 2>&1 &
pid_off=$!
for port in 8811 8812; do
  for _ in $(seq 1 60); do curl -fsS "localhost:$port/healthz" >/dev/null 2>&1 && break || sleep 0.5; done
done

status=0
BASE=http://localhost:8811 node tests/e2e/ui_flows.js "$@" || status=$?
BASE=http://localhost:8812 node tests/e2e/ui_model_off.js || status=$?
if [ "$status" -ne 0 ]; then echo "--- 서버 로그(끝부분) ---"; tail -20 "$work/on.log"; fi
exit "$status"
