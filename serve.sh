#!/usr/bin/env bash
# 로컬에서 대시보드를 보기 위한 정적 서버. 기본 포트 8765.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8765}"
echo "http://localhost:${PORT}/  (Ctrl+C 로 종료)"
exec python3 -m http.server "$PORT" --directory "$ROOT/docs"
