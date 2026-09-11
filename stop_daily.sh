#!/usr/bin/env bash
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$ROOT/logs/daily_loop.pid"
if [[ -f "$PIDFILE" ]]; then
  pid=$(cat "$PIDFILE")
  kill "$pid" 2>/dev/null && echo "daily_loop 중지 (pid $pid)" || echo "이미 종료된 pid $pid"
  rm -f "$PIDFILE"
else
  echo "실행 중인 daily_loop 가 없습니다."
fi
