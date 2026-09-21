#!/usr/bin/env bash
# cron 이 없는 환경용 데몬. 주기가 다른 두 가지를 한 프로세스에서 돌립니다.
#
#   * AI 사용량 스냅샷 — SYNC_EVERY_MIN(기본 5)분마다 받아옵니다.
#     중앙 서버가 5분 주기로 발행하고, 화면은 받아둔 사본만 읽습니다.
#     즉 이 주기가 곧 /ai/ 화면의 신선도입니다.
#   * GPU 공고 수집   — 매일 RUN_AT_KST(기본 09:10)에 한 번 update.sh 를 돌립니다.
#
#   시작: setsid nohup ./daily_loop.sh > /dev/null 2>&1 &
#   중지: ./stop_daily.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_AT_KST="${RUN_AT_KST:-09:10}"
SYNC_EVERY_MIN="${SYNC_EVERY_MIN:-5}"
if [[ -z "${PY:-}" ]]; then
  PY="$ROOT/.venv/bin/python"
  [[ -x "$PY" ]] || PY="$(command -v python3)"
fi
PIDFILE="$ROOT/logs/daily_loop.pid"
STAMPFILE="$ROOT/logs/last_daily_run"      # 수집을 마지막으로 돌린 날짜(KST)
LOG="$ROOT/logs/update.log"
mkdir -p "$ROOT/logs"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "이미 실행 중입니다 (pid $(cat "$PIDFILE"))." >&2
  exit 1
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

# "HH:MM" -> 자정부터의 분. 10# 은 09 를 8진수로 읽지 않게 합니다.
mins() { local t=$1; echo $(( 10#${t%%:*} * 60 + 10#${t##*:} )); }

cd "$ROOT"
# 시작 시각이 오늘 수집 시각을 이미 지났으면 오늘 몫은 끝난 것으로 둡니다.
# (예전처럼 "다음 09:10 까지 기다렸다 한 번" 과 같은 동작. 데몬을 다시 띄웠다고
#  그날 수집이 한 번 더 돌지 않습니다.)
if (( $(mins "$(TZ=Asia/Seoul date +%H:%M)") >= $(mins "$RUN_AT_KST") )); then
  TZ=Asia/Seoul date +%F > "$STAMPFILE"
fi

echo "[daily_loop] 시작 pid=$$ 수집=${RUN_AT_KST} KST 스냅샷=${SYNC_EVERY_MIN}분 간격" >> "$LOG"
while true; do
  # 1) AI 사용량 스냅샷. --quiet 라 바뀐 게 없으면 로그를 남기지 않습니다.
  "$PY" -m scraper.sync_ai_snapshot --quiet >> "$LOG" 2>&1

  # 2) 하루 한 번 GPU 공고 수집. 날짜를 파일에 남겨 두 번 돌지 않게 합니다.
  today=$(TZ=Asia/Seoul date +%F)
  if (( $(mins "$(TZ=Asia/Seoul date +%H:%M)") >= $(mins "$RUN_AT_KST") )) \
     && [[ "$(cat "$STAMPFILE" 2>/dev/null)" != "$today" ]]; then
    echo "$today" > "$STAMPFILE"
    "$ROOT/update.sh"
  fi

  sleep $(( SYNC_EVERY_MIN * 60 ))
done
