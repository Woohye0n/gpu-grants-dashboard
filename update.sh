#!/usr/bin/env bash
# 하루 1회 수집 진입점. cron / daily_loop.sh 가 이 파일을 부릅니다.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-$(command -v python3)}"
LOG="$ROOT/logs/update.log"
mkdir -p "$ROOT/logs"

stamp() { TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST'; }

cd "$ROOT"
{
  echo "===== $(stamp) 수집 시작 ====="
  "$PY" -m scraper.run "$@"
  code=$?
  # 한 파일짜리 사본도 같이 갱신 (공유·오프라인 보관용)
  "$PY" -m scraper.build_single_file >/dev/null 2>&1 || true
  "$PY" -m scraper.build_single_file --artifact >/dev/null 2>&1 || true
  echo "===== $(stamp) 수집 종료 (exit=$code) ====="
  # 캐시는 30일치만 유지 (첨부파일 재다운로드 방지용)
  find "$ROOT/cache" -type f -mtime +30 -delete 2>/dev/null
  exit $code
} >> "$LOG" 2>&1
