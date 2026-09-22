#!/usr/bin/env bash
# 국내 서버에서 공고를 수집하고, 바뀌었으면 저장소에 올린다.
#
#   ./deploy/collect_and_publish.sh
#
# GitHub 러너(해외 IP)에서는 AICA 가 자동등록방지 페이지를, KISTI 가 연결 차단을
# 내줘서 두 곳이 늘 '직전 데이터' 였다. 이 서버는 국내라 둘 다 정상으로 읽힌다
# (실측: 7곳 전부 OK, 그 둘에서만 접수중 공고 4건이 새로 잡힌다). 그래서 수집은
# 여기서 돌리고, 러너의 daily-collect 는 비상용 수동 실행으로 남겨 둔다.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"; command -v "$PY" >/dev/null 2>&1 || PY=python3
LOG="$ROOT/logs/collect.log"; mkdir -p "$ROOT/logs"
say() { echo "[$(TZ=Asia/Seoul date '+%F %H:%M KST')] $*"; }

# 남이 올린 것 위에 얹는다(러너가 수동 실행됐을 수 있다).
git pull -q --rebase --autostash origin main 2>/dev/null || say "pull 실패 — 로컬 상태로 진행"

"$ROOT/update.sh"; code=$?
say "수집 종료 코드 $code (0 정상 / 1 일부 직전데이터 / 2 실패)"

PATHS=(docs/data.js docs/data.json docs/index.html data/history data/last_good)
git add -- "${PATHS[@]}" 2>/dev/null
if git diff --staged --quiet -- "${PATHS[@]}"; then
  say "바뀐 공고 없음 — 올리지 않음"
  exit 0
fi
git -c user.name=gpu-grants-bot -c user.email=gpu-grants-bot@localhost \
    commit -q -m "chore: $(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M KST') 공고 수집 (국내 서버)"
if git push -q origin main; then
  say "올림: $(git rev-parse --short HEAD)"
else
  say "push 실패 — 다음 수집 때 다시 시도합니다"
  exit 1
fi
