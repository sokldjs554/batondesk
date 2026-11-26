#!/usr/bin/env bash
# 화면 점검 실행기: 규칙 엔진 서버와 모델 꺼진 서버를 띄우고 브라우저 흐름을 돌린 뒤 정리한다.
#   tests/e2e/run.sh              # 두 화면 크기 모두
#   SCALE=2 SHOTS_DIR=docs/images/ui tests/e2e/run.sh   # 사람이 볼 캡처(2배 해상도)
set -euo pipefail
cd "$(dirname "$0")/../.."
work=$(mktemp -d)
cleanup() { kill "${pid_on:-}" "${pid_off:-}" "${pid_rec:-}" 2>/dev/null || true; rm -rf "$work"; }
trap cleanup EXIT

# 세 서버를 띄운다.
#   8811 규칙 엔진, 저장 기록 없음   — 대화·인계·오류 흐름과 빈 화면
#   8812 모델 꺼짐, 저장 기록 없음   — 아무것도 없을 때의 화면
#   8813 모델 꺼짐, 저장 기록 있음   — 키 없이 뜬 공개 데모가 보여 주는 화면(실제 모델로 만든 기록을 읽기 전용으로)
BATONDESK_LLM_MODE=scripted BATONDESK_LOAD_RECORDINGS=false BATONDESK_DB_PATH="$work/on.sqlite3" batondesk serve --port 8811 >"$work/on.log" 2>&1 &
pid_on=$!
env -u ANTHROPIC_API_KEY BATONDESK_LLM_MODE=off BATONDESK_LOAD_RECORDINGS=false BATONDESK_DB_PATH="$work/off.sqlite3" batondesk serve --port 8812 >"$work/off.log" 2>&1 &
pid_off=$!
env -u ANTHROPIC_API_KEY BATONDESK_LLM_MODE=off BATONDESK_DB_PATH="$work/rec.sqlite3" batondesk serve --port 8813 >"$work/rec.log" 2>&1 &
pid_rec=$!
for port in 8811 8812 8813; do
  for _ in $(seq 1 60); do curl -fsS "localhost:$port/healthz" >/dev/null 2>&1 && break || sleep 0.5; done
done

status=0
BASE=http://localhost:8811 node tests/e2e/ui_flows.js "$@" || status=$?
BASE=http://localhost:8812 node tests/e2e/ui_model_off.js || status=$?
BASE=http://localhost:8813 node tests/e2e/ui_recordings.js || status=$?
if [ "$status" -ne 0 ]; then echo "--- 서버 로그(끝부분) ---"; tail -20 "$work/on.log"; fi
exit "$status"
