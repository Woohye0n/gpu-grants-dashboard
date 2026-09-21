#!/usr/bin/env bash
# 사내 웹서버가 떠 있는지 확인하고, 없으면 띄운다. cron 이 주기적으로 부른다.
# 이미 떠 있으면 아무것도 하지 않으므로 몇 번을 불러도 안전하다.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8808}"
PIDFILE="$ROOT/logs/serve.pid"
mkdir -p "$ROOT/logs"
alive() {
  local pid="$1"
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  # pid 재사용에 속지 않게 실제 명령줄을 확인한다
  [ -r "/proc/$pid/cmdline" ] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'http.server'
}
if [ -f "$PIDFILE" ] && alive "$(cat "$PIDFILE" 2>/dev/null)"; then
  exit 0
fi
nohup "$ROOT/deploy/serve.sh" "$PORT" >> "$ROOT/logs/serve.log" 2>&1 &
echo $! > "$PIDFILE"
echo "[$(TZ=Asia/Seoul date '+%F %H:%M KST')] serve 기동 pid $(cat "$PIDFILE") :$PORT" >> "$ROOT/logs/serve.log"
