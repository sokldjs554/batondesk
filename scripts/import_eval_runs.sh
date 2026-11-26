#!/usr/bin/env bash
# eval-runs 브랜치(Actions가 실제 모델 평가 결과를 남기는 곳)에서 실행 결과를 가져와 main 의 eval_results/ 에 둔다.
# 대화 전문과 실행 기록이 든 trajectories.jsonl.gz 는 용량 때문에 main 에 넣지 않는다(eval-runs 브랜치와 아티팩트에 있다).
#   scripts/import_eval_runs.sh            # 전부
#   scripts/import_eval_runs.sh v2-test    # 이름에 이 글자가 든 실행만
set -euo pipefail
cd "$(dirname "$0")/.."
filter="${1:-}"
git fetch -q origin eval-runs
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git archive FETCH_HEAD runs recordings 2>/dev/null | tar -x -C "$work" || git archive FETCH_HEAD runs | tar -x -C "$work"
mkdir -p eval_results/runs
for dir in "$work"/runs/*/; do
  name=$(basename "$dir")
  if [ -n "$filter" ] && [[ "$name" != *"$filter"* ]]; then continue; fi
  mkdir -p "eval_results/runs/$name"
  for file in manifest.json summary.json results.jsonl errors.jsonl; do
    [ -f "$dir/$file" ] && cp "$dir/$file" "eval_results/runs/$name/$file"
  done
  echo "가져옴: $name"
done
if [ -d "$work/recordings" ] && ls "$work"/recordings/*.json >/dev/null 2>&1; then
  mkdir -p data/recordings
  cp "$work"/recordings/*.json data/recordings/
  echo "저장된 상담 기록: $(ls data/recordings/*.json | wc -l)건"
fi
python -m batondesk.evals index
