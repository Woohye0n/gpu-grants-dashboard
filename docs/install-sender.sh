#!/usr/bin/env bash
# AI 사용량 송신기 한 줄 설치기 — 이 서버의 모든 사용자에게 깔고 켠다.
#
#   curl -fsSL https://woohye0n.github.io/gpu-grants-dashboard/install-sender.sh | sudo bash
#
# 이 파일에는 NAS 주소도, 계정도, 비밀번호도 없다 — 공개 주소에 두기 때문이다.
# 이미 이 서버에 설치돼 있던 송신기의 config.json 에서 배워 오고, 그것도 없을
# 때만 터미널에서 물어본다.
#
# 왜 이렇게: 예전 안내는 dist 를 scp 로 받고, 비번을 파일로 쓰고, 설치하고, 파일을
# 지우는 네 단계였다. 서버가 여러 대면 매번 정확히 반복해야 하고 한 번만 틀려도
# 조용히 실패한다 — 실제로 그렇게 실패했다.
set -uo pipefail
DIST_NAME="ai-monitoring-send-dist"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

say() { printf '%s\n' "$*"; }
ask() {   # curl | bash 로 실행되므로 stdin 이 아니라 터미널에서 읽는다
  local prompt="$1" var="$2" silent="${3:-}" val=""
  # 파일이 있는 것과 열리는 것은 다르다(cron·nohup 에는 제어 터미널이 없다).
  if ! { : > /dev/tty; } 2>/dev/null; then
    echo "터미널이 없어 입력받을 수 없습니다 — 터미널에서 직접 실행하세요." >&2
    exit 1
  fi
  printf '%s' "$prompt" > /dev/tty
  if [ -n "$silent" ]; then read -rs val < /dev/tty; echo > /dev/tty; else read -r val < /dev/tty; fi
  printf -v "$var" '%s' "$val"
}

[ "$(id -u)" = "0" ] || { echo "root 로 실행하세요:  curl ... | sudo bash" >&2; exit 1; }

# ---- 1) 이 서버에 이미 있는 설치에서 NAS 접근 정보를 배운다 ----------------
cat > "$WORK/readcfg.py" <<'PYEOF'
import json, os, shlex, sys
try:
    c = json.load(open(os.environ["CFGPATH"], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
t = c.get("transport") or {}
for k, v in (("NAS_ROOT", c.get("nas_root")), ("SSH_HOST", t.get("ssh_host")),
             ("SSH_PORT", t.get("ssh_port")), ("SSH_USER", t.get("ssh_user")),
             ("SSH_PW", t.get("ssh_password")), ("SSH_KEY", t.get("ssh_key")),
             ("REMOTE_ROOT", t.get("remote_root"))):
    print(f"{k}={shlex.quote(str(v if v is not None else ''))}")
PYEOF

say "1/3  기존 설치에서 NAS 접근 정보 찾는 중…"
NAS_ROOT=""; SSH_HOST=""; SSH_PORT=""; SSH_USER=""; SSH_PW=""; SSH_KEY=""; REMOTE_ROOT=""
CFG=""
# 읽을 수 있는 것 중 가장 최근 것을 쓴다. root 면 전부 읽히지만, 권한이 없거나
# 깨진 파일 하나 때문에 멈추면 안 된다 -- 다음 후보로 넘어간다.
CANDS="$(find /home /root -maxdepth 4 -name config.json -path '*ai-monitoring-send*' \
         -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)"
while IFS= read -r cand; do
  [ -n "$cand" ] || continue
  parsed="$(CFGPATH="$cand" python3 "$WORK/readcfg.py" 2>/dev/null)" || continue
  eval "$parsed"
  [ -n "$NAS_ROOT$SSH_HOST" ] || continue
  CFG="$cand"; break
done <<< "$CANDS"
if [ -n "$CFG" ]; then say "     찾음: $CFG"; else say "     없음 - 이 서버의 첫 설치입니다"; fi

# ---- 2) 배포본 가져오기 ----------------------------------------------------
say "2/3  최신 송신기 내려받는 중…"
MOUNT_DIST=""
[ -n "$NAS_ROOT" ] && MOUNT_DIST="$(dirname "$NAS_ROOT")/$DIST_NAME"
if [ -n "$MOUNT_DIST" ] && timeout 5 ls -d "$MOUNT_DIST" >/dev/null 2>&1; then
  cp -r "$MOUNT_DIST" "$WORK/dist" && say "     NAS 마운트에서 복사 (비밀번호 불필요)"
else
  [ -n "$SSH_HOST" ] || ask "     NAS 주소: " SSH_HOST
  [ -n "$SSH_PORT" ] || SSH_PORT=2244
  [ -n "$SSH_USER" ] || ask "     NAS 사용자: " SSH_USER
  [ -n "$REMOTE_ROOT" ] || ask "     NAS 상의 수집 경로 (예: /volume1/.../ai-monitoring): " REMOTE_ROOT
  if [ -z "$SSH_KEY" ] && [ -z "$SSH_PW" ]; then
    ask "     NAS 비밀번호: " SSH_PW silent
    [ -n "$SSH_PW" ] || { echo "     비밀번호가 필요합니다." >&2; exit 1; }
  else
    say "     기존 설치의 자격증명을 씁니다 (다시 묻지 않습니다)"
  fi
  REMOTE_DIST="$(dirname "$REMOTE_ROOT")/$DIST_NAME"
  SSH_HOST="$SSH_HOST" SSH_PORT="$SSH_PORT" SSH_USER="$SSH_USER" \
  SSH_PW="$SSH_PW" SSH_KEY="$SSH_KEY" REMOTE_DIST="$REMOTE_DIST" DEST="$WORK/dist" \
  python3 - <<'PY' || { echo "     내려받기 실패" >&2; exit 1; }
# scp 는 비밀번호를 파이프로 받지 않는다(터미널을 요구한다). 가짜 터미널을 만들어
# 건넨다 — 송신기의 sshcmd.py 가 하는 것과 같은 방법이지만, 설치 전이라 여기 담는다.
import os, pty, sys, time

env = os.environ
argv = ["scp", "-O", "-r", "-P", str(env["SSH_PORT"]),
        "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20"]
if env.get("SSH_KEY"):
    argv += ["-i", env["SSH_KEY"], "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes"]
argv += [f"{env['SSH_USER']}@{env['SSH_HOST']}:{env['REMOTE_DIST']}", env["DEST"]]

pid, fd = pty.fork()
if pid == 0:
    os.execvp(argv[0], argv)
buf, sent, deadline = b"", False, time.time() + 300
while time.time() < deadline:
    try:
        chunk = os.read(fd, 4096)
    except OSError:
        break
    if not chunk:
        break
    buf += chunk
    low = buf.lower()
    if not sent and env.get("SSH_PW") and (b"password:" in low or b"passphrase" in low):
        os.write(fd, env["SSH_PW"].encode() + b"\n"); sent = True; buf = b""
code = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
if code != 0:
    sys.stderr.write(buf.decode("utf-8", "replace")[-400:] + "\n")
sys.exit(code)
PY
  say "     NAS 에서 내려받음"
fi
[ -f "$WORK/dist/scripts/bootstrap.sh" ] || { echo "받은 배포본이 이상합니다" >&2; exit 1; }
chmod +x "$WORK/dist"/*.sh "$WORK/dist"/scripts/*.sh 2>/dev/null

# ---- 3) 전원 설치 ----------------------------------------------------------
say "3/3  설치"
exec "$WORK/dist/scripts/bootstrap.sh" "$@"
