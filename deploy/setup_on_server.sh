#!/usr/bin/env bash
# 이 사이트를 다른 서버에 올리는 설치 스크립트.
#
#   git clone <repo> gpu-grants-dashboard && cd gpu-grants-dashboard
#   ./deploy/setup_on_server.sh
#
# 하는 일: 파이썬 가상환경 + 의존성 → 첫 수집 → (선택) cron 등록 → 웹 서버 안내.
# 이미 있는 것은 건너뛰므로 여러 번 실행해도 안전하다.
# 다른 저장소나 외부 서비스가 필요하지 않다 — 클론 하나로 끝난다.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PORT="${PORT:-8080}"
RUN_AT="${RUN_AT:-09:10}"          # GPU 공고 수집 시각(KST)
SYNC_EVERY="${SYNC_EVERY:-5}"      # AI 사용량 스냅샷 동기화 주기(분)
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
# AI 사용량 스냅샷: ai_snapshot.json 의 출처에서 받아온다. 출처가 아직 없어도
# 저장소에 동봉된 사본으로 화면은 뜬다(대신 '낡았다'고 표시된다).
"$VENV/bin/python" -m scraper.sync_ai_snapshot \
  || echo "  (AI 스냅샷 출처를 아직 못 읽었습니다 — ai_snapshot.json 을 확인하세요. 계속 진행)"
"$VENV/bin/python" -m scraper.run
code=$?
echo "  수집 종료 코드 $code  (0 정상 / 1 일부 직전데이터 / 2 실패)"

say "4/5 자동 실행"
mkdir -p logs
DAILY_CRON="${RUN_AT#*:} ${RUN_AT%:*} * * * $ROOT/update.sh"
SYNC_CRON="*/$SYNC_EVERY * * * * cd $ROOT && $VENV/bin/python -m scraper.sync_ai_snapshot --quiet >> $ROOT/logs/cron.log 2>&1"
if command -v crontab >/dev/null 2>&1; then
  current="$(crontab -l 2>/dev/null)"
  add=""
  printf '%s' "$current" | grep -qF "$ROOT/update.sh"        || add+="$DAILY_CRON"$'\n'
  printf '%s' "$current" | grep -qF "scraper.sync_ai_snapshot" || add+="$SYNC_CRON"$'\n'
  if [ -n "$add" ]; then
    { printf '%s\n' "$current"; printf '%s' "$add"; } | grep -v '^$' | crontab -
    echo "  등록: 공고 수집 매일 $RUN_AT · AI 스냅샷 ${SYNC_EVERY}분마다"
  else
    echo "  이미 등록돼 있습니다"
  fi
  crontab -l | grep -E "update\.sh|sync_ai_snapshot" | sed 's/^/    /'
else
  echo "  crontab 이 없습니다. 데몬 하나로 둘 다 돌릴 수 있습니다:"
  echo "    setsid nohup ./daily_loop.sh > /dev/null 2>&1 &     # 중지: ./stop_daily.sh"
  echo "  직접 등록한다면:"
  echo "    $DAILY_CRON"
  echo "    $SYNC_CRON"
fi

say "5/5 웹 서버"
echo "  ./deploy/serve.sh $PORT    ← 바로 띄우기"
echo "  또는 nginx 에서 root $ROOT/docs;"
echo
echo "설치 끝. http://<이서버>:$PORT/ 에서 열립니다 (AI 사용량은 /ai/)."
