#!/usr/bin/env bash
# cron 이 없는 환경용 데몬: 매일 지정 시각(KST)에 update.sh 를 한 번 실행합니다.
#   시작: setsid nohup ./daily_loop.sh > /dev/null 2>&1 &
#   중지: ./stop_daily.sh
# 실행 시각은 RUN_AT_KST(기본 09:10)로 바꿉니다.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_AT_KST="${RUN_AT_KST:-09:10}"
PIDFILE="$ROOT/logs/daily_loop.pid"
mkdir -p "$ROOT/logs"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "이미 실행 중입니다 (pid $(cat "$PIDFILE"))." >&2
  exit 1
fi
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT

echo "[daily_loop] 시작 pid=$$ 실행시각=${RUN_AT_KST} KST" >> "$ROOT/logs/update.log"
while true; do
  now=$(TZ=Asia/Seoul date +%s)
  today_target=$(TZ=Asia/Seoul date -d "today ${RUN_AT_KST}" +%s 2>/dev/null) || exit 1
  target=$today_target
  (( target <= now )) && target=$(TZ=Asia/Seoul date -d "tomorrow ${RUN_AT_KST}" +%s)
  sleep $(( target - now ))
  "$ROOT/update.sh"
done
