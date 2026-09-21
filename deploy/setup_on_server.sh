#!/usr/bin/env bash
# 이 사이트를 다른 서버(예: ADS)에 올리는 설치 스크립트.
#
#   git clone <repo> gpu-grants-dashboard && cd gpu-grants-dashboard
#   ./deploy/setup_on_server.sh
#
# 하는 일: 파이썬 가상환경 + 의존성 → 첫 수집 → (선택) cron 등록 → (선택) 웹 서버 기동.
# 이미 있는 것은 건너뛰므로 여러 번 실행해도 안전하다.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8080}"
RUN_AT="${RUN_AT:-09:10}"
VENV="$ROOT/.venv"
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "1/5 파이썬 확인"
PY=$(command -v python3) || { echo "python3 가 없습니다"; exit 1; }
"$PY" -c 'import sys; assert sys.version_info>=(3,9), sys.version' 2>/dev/null \
  || { echo "python 3.9 이상이 필요합니다 ($($PY -V))"; exit 1; }
echo "  $PY ($($PY -V 2>&1))"

say "2/5 가상환경 + 의존성"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r requirements.txt
"$VENV/bin/python" -c "import scraper.run, scraper.probe, scraper.add_source; print('  의존성 OK')"

say "3/5 첫 수집 (몇 분 걸립니다)"
"$VENV/bin/python" -m scraper.sync_ai_snapshot || echo "  (AI 스냅샷 동기화 실패 — 계속 진행)"
"$VENV/bin/python" -m scraper.run
code=$?
echo "  수집 종료 코드 $code  (0 정상 / 1 일부 직전데이터 / 2 실패)"

say "4/5 매일 자동 수집"
CRON_LINE="$(printf '%s %s cd %s && %s/bin/python -m scraper.sync_ai_snapshot >>logs/cron.log 2>&1; %s/bin/python -m scraper.run >>logs/cron.log 2>&1' \
  "${RUN_AT#*:}" "${RUN_AT%:*}" "$ROOT" "$VENV" "$VENV")"
mkdir -p logs
if command -v crontab >/dev/null 2>&1; then
  if crontab -l 2>/dev/null | grep -q "$ROOT.*scraper.run"; then
    echo "  이미 등록돼 있습니다"
  else
    ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -
    echo "  등록: 매일 $RUN_AT"
  fi
  crontab -l | grep scraper.run | sed 's/^/    /'
else
  echo "  crontab 이 없습니다. 아래를 직접 등록하세요:"
  echo "    $CRON_LINE"
fi

say "5/5 웹 서버"
cat > "$ROOT/deploy/serve.sh" <<SERVE
#!/usr/bin/env bash
# 정적 파일 서버. nginx 를 쓸 거면 docs/ 를 루트로 잡으면 된다.
cd "\$(dirname "\${BASH_SOURCE[0]}")/.."
exec "$VENV/bin/python" -m http.server "\${1:-$PORT}" --directory docs --bind "\${BIND:-0.0.0.0}"
SERVE
chmod +x "$ROOT/deploy/serve.sh"
echo "  ./deploy/serve.sh $PORT    ← 바로 띄우기"
echo "  또는 nginx 에서 root $ROOT/docs;"
echo
echo "설치 끝. http://<이서버>:$PORT/ 에서 열립니다 (AI 사용량은 /ai/)."
