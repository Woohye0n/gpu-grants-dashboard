#!/usr/bin/env bash
# 집계한 스냅샷을 공개 사이트(GitHub Pages)에 반영한다.
#
#   deploy/publish_pages.sh            # 집계 → 바뀐 게 있으면 발행
#   FORCE=1 deploy/publish_pages.sh    # 바뀐 게 없어도 발행
#
# 스냅샷은 main 이 아니라 **ai-data 라는 고아 브랜치**에 커밋 하나로 force-push
# 합니다. 5분마다 160KB JSON 을 main 에 쌓으면 저장소가 1년이면 수 GB 가 되고,
# 코드 이력도 그 사이에 묻힙니다. 고아 브랜치는 언제나 커밋 1개라 저장소가
# 커지지 않고, 배포 워크플로가 그 파일만 꺼내 docs/ 에 넣어 Pages 로 올립니다.
#
# 인증은 저장소 Deploy key(쓰기 허용) 하나입니다 — 계정 전체 권한이 아니라
# 이 저장소에만 쓸 수 있는 키입니다.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
KEY="${DEPLOY_KEY:-$HOME/.ssh/id_ed25519_gpugrants}"
BRANCH="${DATA_BRANCH:-ai-data}"
SNAPSHOT="docs/ai/data/dashboard.json"
STAMP="$ROOT/logs/.last_published"
mkdir -p "$ROOT/logs"

say() { echo "[$(TZ=Asia/Seoul date '+%F %H:%M KST')] $*"; }

# 1) 최신으로 집계한다 (사내 사이트는 이것만으로 이미 최신이다)
"$PY" -m scraper.sync_ai_snapshot --quiet || { say "집계 실패"; exit 1; }
[ -f "$SNAPSHOT" ] || { say "스냅샷이 없습니다"; exit 1; }

# 2) 사람이 보는 숫자가 실제로 바뀌었을 때만 발행한다.
#    generated_at 은 매번 달라지므로 그것만으로 판단하면 밤새 아무도 안 썼는데도
#    5분마다 배포가 돈다.
FP="$("$PY" - "$SNAPSHOT" <<'PYEOF'
import hashlib, json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
s = d.get("summary") or {}
sig = {
    "totals": s.get("totals"),
    "live": s.get("live_session_count"),
    "accounts": [[a.get("provider"), a.get("email"), a.get("status"),
                  a.get("windows"), a.get("lifetime"),
                  [(t.get("window"), t.get("value")) for t in a.get("thresholds") or []]]
                 for a in s.get("accounts") or []],
    "people": [[p.get("owner"), p.get("windows"), p.get("lifetime"), p.get("live_sessions")]
               for p in s.get("people") or []],
    "alerts": len(d.get("alerts") or []),
}
print(hashlib.sha256(json.dumps(sig, sort_keys=True, default=str).encode()).hexdigest()[:16])
PYEOF
)"
if [ -z "${FORCE:-}" ] && [ "$FP" = "$(cat "$STAMP" 2>/dev/null)" ]; then
  exit 0                                   # 숫자가 그대로 — 배포할 이유가 없다
fi

# 3) 커밋 1개짜리 고아 브랜치로 force-push
if [ ! -f "$KEY" ]; then
  say "배포 키가 없습니다: $KEY  (저장소 Settings→Deploy keys 에 공개키를 등록하세요)"
  exit 2
fi
REMOTE="${PUSH_URL:-git@github.com:Woohye0n/gpu-grants-dashboard.git}"
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
# 별도 인덱스를 쓰므로 작업트리와 스테이징을 건드리지 않는다.
# git 은 **없는 경로**를 받아야 인덱스를 새로 만든다. mktemp 가 만들어 둔 빈 파일을
# 주면 "index file smaller than expected" 로 거부한다.
TMPD="$(mktemp -d)"
export GIT_INDEX_FILE="$TMPD/index"
trap 'rm -rf "$TMPD"' EXIT
BLOB="$(git hash-object -w "$SNAPSHOT")" || { say "blob 생성 실패"; exit 1; }
git update-index --add --cacheinfo "100644,$BLOB,dashboard.json" || exit 1
# push 트리거는 **그 브랜치에 있는** 워크플로를 씁니다. 스냅샷만 올리면 ai-data 에는
# 워크플로가 없어 배포가 아예 돌지 않습니다. 그래서 배포 워크플로를 같이 싣습니다.
# 매번 작업트리에서 다시 읽으므로 main 쪽과 어긋날 일은 없습니다.
WORKFLOW=".github/workflows/pages.yml"
if [ -f "$WORKFLOW" ]; then
  WF_BLOB="$(git hash-object -w "$WORKFLOW")" || exit 1
  git update-index --add --cacheinfo "100644,$WF_BLOB,$WORKFLOW" || exit 1
fi
TREE="$(git write-tree)" || exit 1
COMMIT="$(printf 'chore: AI 사용량 스냅샷 %s\n' "$(TZ=Asia/Seoul date '+%F %H:%M KST')" \
          | git commit-tree "$TREE")" || exit 1
if git push -q -f "$REMOTE" "$COMMIT:refs/heads/$BRANCH" 2>&1; then
  echo "$FP" > "$STAMP"
  say "발행 완료 → $BRANCH ($(du -h "$SNAPSHOT" | cut -f1))"
else
  say "push 실패 — 배포 키가 등록/쓰기허용 돼 있는지 확인하세요"
  exit 3
fi
