#!/usr/bin/env bash
# 서버에 올릴 때 쓰는 정적 파일 서버. nginx 를 쓸 거면 docs/ 를 루트로 잡으면 됩니다.
#   ./deploy/serve.sh 8080        포트는 인자 또는 PORT
#   BIND=127.0.0.1 ./deploy/serve.sh   바깥에 열지 않고 띄우기
# 경로를 박아두지 않습니다 — 어느 서버에 클론해도 그대로 돕니다.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
exec "$PY" -m http.server "${1:-${PORT:-8080}}" --directory "$ROOT/docs" --bind "${BIND:-0.0.0.0}"
