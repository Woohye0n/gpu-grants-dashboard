"""AI 사용량 배치(NAS inbox)를 이 저장소 안의 SQLite 로 모은다.

예전에는 중앙 서버(다른 사람 홈의 backend + monitoring.db)가 inbox 를 읽어
스냅샷을 만들어 다른 깃헙 저장소로 발행하고, 이 사이트는 그걸 받아 썼습니다.
그 발행이 2026-09-16 에 멈췄고, 멈춘 줄도 모른 채 4.5일이 흘렀습니다. 수집기와
inbox 는 멀쩡했는데 중간 한 칸이 빠져서 화면 전체가 과거에 멈춘 것입니다.

그래서 그 칸을 이 저장소 안으로 가져왔습니다. inbox 는 송신기들이 직접 쓰는
디렉토리이므로, 여기만 읽으면 **다른 사람의 프로세스도, 다른 저장소도 필요 없습니다.**

저장 구조 — 두 가지를 나눠 담습니다.
  session_totals : 세션별 누적(= 화면의 세션 표와 누적 토큰). 행 수가 세션 수로
                   묶이므로 오래 돌려도 커지지 않습니다.
  usage          : 최근 구간의 턴 단위 기록. 5시간/주간 창을 세려면 시각이 필요한데,
                   그건 최근 것만 있으면 됩니다(retain_days 로 정리).
  usage_daily    : 하루 × 세션 × 모델 × 속도 × effort 로 묶은 기록. '기록' 탭이 쓴다.
                   usage 를 지우기 **전에** 묶어 두고 지우지 않으므로, 턴 기록이
                   사라진 뒤에도 지난 기간을 볼 수 있습니다(refresh_daily).
"""
from __future__ import annotations

import glob
import re
import gzip
import json
import os
import sqlite3
import time

from .common import ROOT

DB_PATH = os.path.join(ROOT, "data", "ai", "usage.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    path TEXT PRIMARY KEY, size INTEGER, mtime REAL, ingested_at INTEGER);

-- 턴 단위. uuid 가 턴의 정체성이라, 송신기가 재시작해 같은 배치를 다시 보내도
-- 두 번 세지 않는다.
--
-- speed / cache_creation_1h 는 토큰당 단가를 가르는 값이다(ai_pricing 참고).
--   speed             : standard | fast | flex | ... (Codex 는 처리 티어를 옮긴 값)
--   cache_creation_1h : 캐시 쓰기 중 1시간 TTL 몫. NULL 이면 TTL 을 모른다
--                       (구버전 송신기) — 0 과는 뜻이 다르다.
--   effort            : 단가는 바꾸지 않고 생성량만 바꾼다. 분류 용도.
CREATE TABLE IF NOT EXISTS usage (
    uuid TEXT PRIMARY KEY, ts INTEGER, provider TEXT, host TEXT,
    account_email TEXT, session_id TEXT, model TEXT, surface TEXT,
    input INTEGER, output INTEGER, cache_creation INTEGER, cache_read INTEGER,
    speed TEXT, effort TEXT, cache_creation_1h INTEGER);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE INDEX IF NOT EXISTS usage_session ON usage(session_id);

-- 세션 누적. usage 를 오래 보관하지 않아도 누적 토큰과 세션 표가 유지된다.
CREATE TABLE IF NOT EXISTS session_totals (
    provider TEXT, session_id TEXT, account_email TEXT, host TEXT,
    cwd TEXT, project TEXT, first_ts INTEGER, last_ts INTEGER,
    messages INTEGER DEFAULT 0, input INTEGER DEFAULT 0, output INTEGER DEFAULT 0,
    cache_creation INTEGER DEFAULT 0, cache_read INTEGER DEFAULT 0,
    models TEXT DEFAULT '[]', surfaces TEXT DEFAULT '[]',
    PRIMARY KEY (provider, session_id));

-- 송신기가 보내는 세션 행(라이브 여부·pid·버전). usage 가 없어도 존재한다.
CREATE TABLE IF NOT EXISTS session_meta (
    provider TEXT, session_id TEXT, host TEXT, account_email TEXT,
    cwd TEXT, project TEXT, version TEXT, kind TEXT, entrypoint TEXT,
    surface TEXT, status TEXT, pid INTEGER, pid_alive INTEGER,
    started_at INTEGER, updated_at INTEGER, reported_at INTEGER,
    PRIMARY KEY (provider, session_id));

CREATE TABLE IF NOT EXISTS accounts (
    provider TEXT, email TEXT, account_id TEXT, org_type TEXT,
    rate_limit_tier TEXT, display_name TEXT, org_name TEXT,
    rate_limits TEXT, rate_limits_updated_at INTEGER,
    usage_status TEXT, usage_status_at INTEGER, last_seen INTEGER,
    PRIMARY KEY (provider, email));

CREATE TABLE IF NOT EXISTS nodes (
    host TEXT PRIMARY KEY, os_user TEXT, sender_root TEXT, fqdn TEXT,
    ip TEXT, machine_id TEXT, home TEXT, last_report INTEGER,
    diagnostics TEXT);

-- 한도 초과 이력. 중앙 서버가 갖고 있던 것을 여기서 이어 간다.
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, name TEXT,
    account TEXT, provider TEXT, window TEXT, metric TEXT,
    value REAL, limit_value REAL, message TEXT);
CREATE INDEX IF NOT EXISTS alerts_ts ON alerts(ts);

-- 기록 탭용 하루 단위 묶음. src='local' 은 이 DB 의 usage 에서, 'central' 은 이 DB 가
-- 생기기 전 기간을 중앙 서버에서 한 번 옮겨 온 것(import_daily). 세션·작업 경로를
-- 남겨 두므로 사람 규칙을 고치면 과거도 다시 갈린다.
CREATE TABLE IF NOT EXISTS usage_daily (
    day TEXT NOT NULL, src TEXT NOT NULL, provider TEXT NOT NULL,
    account_email TEXT NOT NULL DEFAULT '', host TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '', cwd TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '', speed TEXT NOT NULL DEFAULT 'standard',
    effort TEXT NOT NULL DEFAULT '', lc INTEGER NOT NULL DEFAULT 0,
    n INTEGER, i INTEGER, o INTEGER, cw INTEGER, cr INTEGER, cw1h INTEGER, cwu INTEGER,
    PRIMARY KEY (day, src, provider, account_email, host, session_id, cwd,
                 model, speed, effort, lc));
"""


def connect(path=DB_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    # 읽는 쪽(빌드)과 쓰는 쪽(수집)이 겹쳐도 서로를 막지 않게.
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    migrate(db)
    return db


# 이미 돌고 있는 DB 에 나중에 생긴 컬럼. CREATE TABLE IF NOT EXISTS 는 있는 표를
# 건드리지 않으므로 따로 붙인다.
_ADDED_COLUMNS = {"usage": (("speed", "TEXT"), ("effort", "TEXT"),
                            ("cache_creation_1h", "INTEGER"))}


def migrate(db):
    """옛 DB 를 지금 모양으로 맞춘다. 몇 번을 불러도 결과가 같다."""
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, kind in cols:
            if name not in have:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    if meta_get(db, "codex_uuid_v2") != "done":
        n = rekey_codex_usage(db)
        meta_set(db, "codex_uuid_v2", "done")
        if n:
            meta_set(db, "codex_uuid_v2_removed", n)
    db.commit()


def codex_usage_uuid(session_id, model, input_tokens, output_tokens, cache_read_tokens):
    """Codex 턴의 정체성 — 송신기 ``sender/codex_collector.py:usage_uuid`` 와 같은 식.

    송신기가 예전에 쓰던 키(``codex:{sid}:{ts}:{seq}``)는 파일을 어디서부터 읽었는지에
    딸려 움직였다. 세션을 resume 하면 Codex 가 이전 턴들을 재개 시각으로 다시 적으므로
    같은 턴이 다른 키로 거듭 들어왔다. 같은 inbox 를 읽는 다른 집계에서 측정하니 세션
    하나가 20배(실제 69,275 턴 → 1,396,827 행), 커서가 생긴 9/23 이후에도 노드 하나가
    1.95배였다.

    송신기는 서버마다 손으로 올리므로 버전이 섞인다. 그래서 여기서 **다시 계산**한다 —
    어느 버전의 송신기가 보냈든 같은 턴은 같은 키가 된다. 식을 바꾸면 송신기 쪽도
    같이 바꿔야 한다.
    """
    import hashlib
    key = "|".join(str(x) for x in (
        session_id, model, input_tokens, output_tokens, cache_read_tokens))
    return "codex:" + hashlib.sha1(key.encode("utf-8")).hexdigest()


def rekey_codex_usage(db):
    """이미 쌓인 codex 행을 새 키로 접는다. 같은 턴이면 **가장 이른 시각** 하나만 남긴다.

    재개분은 재개 시각을 달고 오므로, 남겨야 할 것은 원래 턴이 실제로 돈 시각이다.
    세션 누적(session_totals)은 이미 부풀어 있던 만큼 되돌리지 못한다 — 턴 기록이
    retain_days 만큼만 남아 있어서 다시 셀 근거가 없다. 창(5시간/주간)은 턴 기록으로
    세므로 이 정리로 바로 맞아진다.
    """
    before = db.execute("SELECT COUNT(*) FROM usage WHERE provider='codex'").fetchone()[0]
    if not before:
        return 0
    # SQLite 는 MIN() 과 함께 고른 맨 컬럼(rowid)을 그 최솟값을 가진 행에서 가져온다.
    db.execute("""DELETE FROM usage WHERE provider='codex' AND rowid NOT IN (
                    SELECT rid FROM (SELECT rowid AS rid, MIN(ts) FROM usage
                                     WHERE provider='codex'
                                     GROUP BY session_id, model, input, output, cache_read))""")
    rows = db.execute("""SELECT uuid, session_id, model, input, output, cache_read
                         FROM usage WHERE provider='codex'""").fetchall()
    changes = []
    for r in rows:
        new = codex_usage_uuid(r["session_id"], r["model"], r["input"],
                               r["output"], r["cache_read"])
        if new != r["uuid"]:
            changes.append((new, r["uuid"]))
    db.executemany("UPDATE usage SET uuid=? WHERE uuid=?", changes)
    after = db.execute("SELECT COUNT(*) FROM usage WHERE provider='codex'").fetchone()[0]
    return before - after


def _num(v):
    return int(v) if isinstance(v, (int, float)) else 0


# 표면 이름이 도구마다 다르게 오고, 노드는 저마다의 일정으로 올라간다.
# 구버전 송신기가 못 알아본 값은 other:<원문> 으로 실려 오므로, 원문을 구분자로
# 쪼갠 토큰이 아는 표면이면 중앙에서 되돌린다. 전부 올릴 때까지 대시보드에
# 같은 표면이 두 개로 갈리는 것을 막는다.
_SURFACE_WORDS = {
    "cli": "terminal", "terminal": "terminal", "tui": "terminal", "shell": "terminal",
    "vscode": "vscode", "code": "vscode",
    "desktop": "desktop", "app": "desktop",
}


def _surface(value):
    if not isinstance(value, str) or not value.startswith("other:"):
        return value
    parts = re.split(r"[_\-\s]", value[len("other:"):].strip().lower())
    for part in parts:
        fixed = _SURFACE_WORDS.get(part)
        if fixed:
            return fixed
    return value


def _ttl_1h(u):
    """캐시 쓰기 중 1시간 TTL 몫. 송신기가 TTL 을 안 보냈으면 None(=모름)."""
    h1, m5 = u.get("cache_creation_1h_tokens"), u.get("cache_creation_5m_tokens")
    if isinstance(h1, (int, float)):
        return int(h1)
    if isinstance(m5, (int, float)):
        return max(0, _num(u.get("cache_creation_tokens")) - int(m5))
    if (u.get("provider") or "claude") == "codex":
        return 0          # OpenAI 캐시 쓰기에는 TTL 구분이 없다 — 모르는 게 아니다
    return None


def _merge_list(raw, values):
    """세션이 여러 모델/표면을 오가므로 합집합으로 모은다."""
    try:
        have = set(json.loads(raw) or [])
    except (ValueError, TypeError):
        have = set()
    have.update(v for v in values if v)
    return json.dumps(sorted(have), ensure_ascii=False)


def ingest_batch(db, payload, host_hint=None):
    """배치 하나를 반영한다. 같은 배치를 다시 넣어도 결과가 같다(uuid 기준)."""
    host = payload.get("host") or host_hint
    now = int(time.time() * 1000)

    db.execute("""INSERT INTO nodes(host, os_user, sender_root, fqdn, ip, machine_id,
                                    home, last_report, diagnostics)
                  VALUES(?,?,?,?,?,?,?,?,?)
                  ON CONFLICT(host) DO UPDATE SET
                    os_user=excluded.os_user, sender_root=excluded.sender_root,
                    fqdn=excluded.fqdn, ip=excluded.ip,
                    machine_id=COALESCE(excluded.machine_id, nodes.machine_id),
                    home=excluded.home,
                    last_report=MAX(COALESCE(nodes.last_report,0), COALESCE(excluded.last_report,0)),
                    diagnostics=COALESCE(excluded.diagnostics, nodes.diagnostics)""",
               (host, payload.get("os_user"), payload.get("sender_root"),
                payload.get("fqdn"), payload.get("ip"), payload.get("machine_id"),
                payload.get("home"), payload.get("generated_at") or now,
                json.dumps(payload.get("diagnostics"), ensure_ascii=False)
                if payload.get("diagnostics") else None))

    for a in payload.get("accounts") or []:
        email = a.get("email")
        if not email:
            continue
        db.execute("""INSERT INTO accounts(provider, email, account_id, org_type,
                        rate_limit_tier, display_name, org_name, rate_limits,
                        rate_limits_updated_at, usage_status, usage_status_at, last_seen)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(provider, email) DO UPDATE SET
                        account_id=COALESCE(excluded.account_id, accounts.account_id),
                        org_type=COALESCE(excluded.org_type, accounts.org_type),
                        rate_limit_tier=COALESCE(excluded.rate_limit_tier, accounts.rate_limit_tier),
                        display_name=COALESCE(excluded.display_name, accounts.display_name),
                        org_name=COALESCE(excluded.org_name, accounts.org_name),
                        -- 한도는 더 최근에 읽은 쪽만 이긴다. 오래된 노드의 배치가
                        -- 나중에 도착했다고 신선한 값을 덮으면 안 된다.
                        rate_limits=CASE WHEN COALESCE(excluded.rate_limits_updated_at,0)
                                              >= COALESCE(accounts.rate_limits_updated_at,0)
                                         AND excluded.rate_limits IS NOT NULL
                                    THEN excluded.rate_limits ELSE accounts.rate_limits END,
                        rate_limits_updated_at=MAX(COALESCE(accounts.rate_limits_updated_at,0),
                                                   COALESCE(excluded.rate_limits_updated_at,0)),
                        usage_status=CASE WHEN COALESCE(excluded.usage_status_at,0)
                                               >= COALESCE(accounts.usage_status_at,0)
                                          AND excluded.usage_status IS NOT NULL
                                     THEN excluded.usage_status ELSE accounts.usage_status END,
                        usage_status_at=MAX(COALESCE(accounts.usage_status_at,0),
                                            COALESCE(excluded.usage_status_at,0)),
                        last_seen=MAX(COALESCE(accounts.last_seen,0), COALESCE(excluded.last_seen,0))""",
                   (a.get("provider") or "claude", email, a.get("account_id"),
                    a.get("org_type"), a.get("rate_limit_tier"), a.get("display_name"),
                    a.get("org_name"),
                    json.dumps(a.get("rate_limits"), ensure_ascii=False) if a.get("rate_limits") else None,
                    a.get("rate_limits_updated_at"), a.get("usage_status"),
                    a.get("usage_status_at"), payload.get("generated_at") or now))

    for s in payload.get("sessions") or []:
        sid = s.get("session_id")
        if not sid:
            continue
        db.execute("""INSERT INTO session_meta(provider, session_id, host, account_email,
                        cwd, project, version, kind, entrypoint, surface, status,
                        pid, pid_alive, started_at, updated_at, reported_at)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(provider, session_id) DO UPDATE SET
                        host=excluded.host,
                        account_email=COALESCE(excluded.account_email, session_meta.account_email),
                        -- cwd 는 사람을 가르는 유일한 근거다. 새 보고가 비어 있다고
                        -- 이미 알던 값을 지우면 그 세션은 누구에게도 안 붙는다.
                        cwd=COALESCE(excluded.cwd, session_meta.cwd),
                        project=COALESCE(excluded.project, session_meta.project),
                        version=COALESCE(excluded.version, session_meta.version),
                        kind=COALESCE(excluded.kind, session_meta.kind),
                        entrypoint=COALESCE(excluded.entrypoint, session_meta.entrypoint),
                        surface=COALESCE(excluded.surface, session_meta.surface),
                        status=excluded.status, pid=excluded.pid,
                        pid_alive=excluded.pid_alive,
                        started_at=COALESCE(excluded.started_at, session_meta.started_at),
                        updated_at=MAX(COALESCE(session_meta.updated_at,0),
                                       COALESCE(excluded.updated_at,0)),
                        reported_at=excluded.reported_at""",
                   (s.get("provider") or "claude", sid, host, s.get("account_email"),
                    s.get("cwd"), s.get("project"), s.get("version"), s.get("kind"),
                    s.get("entrypoint"), _surface(s.get("surface")), s.get("status"),
                    s.get("pid"), s.get("pid_alive"), s.get("started_at"),
                    s.get("updated_at"), payload.get("generated_at") or now))

    added = 0
    seed_until = int(meta_get(db, "seed_until_ms", 0) or 0)
    for u in payload.get("usage") or []:
        uid = u.get("uuid")
        if uid and (u.get("provider") or "claude") == "codex":
            uid = codex_usage_uuid(u.get("session_id"), u.get("model"),
                                   _num(u.get("input_tokens")), _num(u.get("output_tokens")),
                                   _num(u.get("cache_read_tokens")))
        if seed_until and (u.get("ts") or 0) <= seed_until:
            continue                      # 시드 스냅샷이 이미 세어 둔 구간
        # 계정을 증명하지 못한 기록은 집계하지 않는다(송신기가 그렇게 표시해 보낸다).
        if not uid or u.get("assumed") or not u.get("account_email"):
            continue
        cur = db.execute(
            """INSERT OR IGNORE INTO usage(uuid, ts, provider, host, account_email,
                 session_id, model, surface, input, output, cache_creation, cache_read,
                 speed, effort, cache_creation_1h)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, u.get("ts"), u.get("provider") or "claude", host, u["account_email"],
             u.get("session_id"), u.get("model"), _surface(u.get("surface")),
             _num(u.get("input_tokens")), _num(u.get("output_tokens")),
             _num(u.get("cache_creation_tokens")), _num(u.get("cache_read_tokens")),
             u.get("speed"), u.get("effort"), _ttl_1h(u)))
        if not cur.rowcount:
            continue                      # 이미 센 턴
        added += 1
        sid = u.get("session_id") or f"_{uid}"
        row = db.execute("SELECT models, surfaces FROM session_totals WHERE provider=? AND session_id=?",
                         (u.get("provider") or "claude", sid)).fetchone()
        models = _merge_list(row["models"] if row else "[]", [u.get("model")])
        surfaces = _merge_list(row["surfaces"] if row else "[]", [_surface(u.get("surface"))])
        db.execute("""INSERT INTO session_totals(provider, session_id, account_email, host,
                        cwd, project, first_ts, last_ts, messages, input, output,
                        cache_creation, cache_read, models, surfaces)
                      VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?,?,?)
                      ON CONFLICT(provider, session_id) DO UPDATE SET
                        account_email=COALESCE(excluded.account_email, session_totals.account_email),
                        host=COALESCE(excluded.host, session_totals.host),
                        cwd=COALESCE(excluded.cwd, session_totals.cwd),
                        project=COALESCE(excluded.project, session_totals.project),
                        first_ts=MIN(COALESCE(session_totals.first_ts, excluded.first_ts),
                                     COALESCE(excluded.first_ts, session_totals.first_ts)),
                        last_ts=MAX(COALESCE(session_totals.last_ts,0), COALESCE(excluded.last_ts,0)),
                        messages=session_totals.messages + 1,
                        input=session_totals.input + excluded.input,
                        output=session_totals.output + excluded.output,
                        cache_creation=session_totals.cache_creation + excluded.cache_creation,
                        cache_read=session_totals.cache_read + excluded.cache_read,
                        models=excluded.models, surfaces=excluded.surfaces""",
                   (u.get("provider") or "claude", sid, u["account_email"], host,
                    u.get("cwd"), u.get("project"), u.get("ts"), u.get("ts"),
                    _num(u.get("input_tokens")), _num(u.get("output_tokens")),
                    _num(u.get("cache_creation_tokens")), _num(u.get("cache_read_tokens")),
                    models, surfaces))
    return added


def _read(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rb") as fh:
        return json.load(fh)


def ingest_inbox(db, inbox, ignore_hosts=(), progress=None):
    """아직 안 읽은 배치만 반영한다. 파일 크기/시각이 같으면 건너뛴다."""
    ignore = set(ignore_hosts or ())
    seen = {r["path"]: (r["size"], r["mtime"])
            for r in db.execute("SELECT path, size, mtime FROM batches")}
    # `batch-*.json*` 는 전송 중 임시파일(`.json.gz.building`)까지 물어온다.
    # 실제로 inbox 에 0바이트짜리가 797개 남아 있었다 — 배송이 끊긴 흔적이다.
    files = sorted(f for f in glob.glob(os.path.join(inbox, "*", "batch-*.json*"))
                   if f.endswith((".json", ".json.gz")))
    new, rows, skipped, failed = 0, 0, 0, 0
    for i, path in enumerate(files):
        host = os.path.basename(os.path.dirname(path))
        if host in ignore:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not st.st_size:
            continue                      # 반쪽 배송의 잔해
        if seen.get(path) == (st.st_size, st.st_mtime):
            skipped += 1
            continue
        try:
            payload = _read(path)
        except Exception:                                   # noqa: BLE001
            failed += 1
            continue
        rows += ingest_batch(db, payload, host_hint=host)
        db.execute("""INSERT INTO batches(path, size, mtime, ingested_at) VALUES(?,?,?,?)
                      ON CONFLICT(path) DO UPDATE SET size=excluded.size,
                        mtime=excluded.mtime, ingested_at=excluded.ingested_at""",
                   (path, st.st_size, st.st_mtime, int(time.time() * 1000)))
        new += 1
        if new % 50 == 0:
            db.commit()
            if progress:
                progress(i + 1, len(files), new, rows)
    db.commit()
    return {"batches": new, "skipped": skipped, "failed": failed, "usage_rows": rows,
            "total_batches": len(files)}


def meta_get(db, key, default=None):
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(db, key, value):
    db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    db.commit()


def seed_from_snapshot(db, path):
    """이전에 발행되던 스냅샷에서 **세션 누적**을 가져와 출발선을 맞춘다.

    inbox 는 각 송신기가 72시간만 남기므로, 여기서 새로 시작하면 '누적 토큰'이
    0부터 다시 셉니다. 마지막 스냅샷에는 세션별 누적이 들어 있으니 그것을 기준선으로
    깔고, 그 시점 이전의 턴은 이미 반영된 것으로 보고 건너뜁니다(이중 계산 방지).
    """
    with open(path, "rb") as fh:
        snap = json.load(fh)
    cutoff = int(snap.get("generated_at") or 0)
    n = 0
    for s in snap.get("sessions") or []:
        sid, prov = s.get("session_id"), s.get("provider") or "claude"
        if not sid:
            continue
        m = s.get("metrics") or {}
        db.execute("""INSERT INTO session_totals(provider, session_id, account_email, host,
                        cwd, project, first_ts, last_ts, messages, input, output,
                        cache_creation, cache_read, models, surfaces)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'[]')
                      ON CONFLICT(provider, session_id) DO NOTHING""",
                   (prov, sid, s.get("account_email"), s.get("host"), s.get("cwd"),
                    s.get("project"), s.get("first_ts"), s.get("last_ts"),
                    _num(s.get("messages")), _num(m.get("input")), _num(m.get("output")),
                    _num(m.get("cache_creation")), _num(m.get("cache_read")),
                    json.dumps(s.get("models") or [], ensure_ascii=False)))
        n += 1
    for a in snap.get("alerts") or []:
        if not a.get("ts"):
            continue
        db.execute("""INSERT INTO alerts(ts, name, account, provider, window, metric,
                        value, limit_value, message) VALUES(?,?,?,?,?,?,?,?,?)""",
                   (a.get("ts"), a.get("name"), a.get("account"), a.get("provider"),
                    a.get("window"), a.get("metric"), a.get("value"),
                    a.get("limit_value"), a.get("message")))
    meta_set(db, "seed_until_ms", cutoff)
    meta_set(db, "seeded_from", os.path.abspath(path))
    db.commit()
    return {"sessions": n, "seed_until_ms": cutoff}


def prune(db, retain_days=30):
    """턴 단위 기록은 창 계산에만 쓰이므로 오래된 것은 버린다.

    세션 누적(session_totals)은 그대로 남으므로 '누적 토큰'은 줄지 않는다.
    """
    if not retain_days:
        return 0
    cutoff = int((time.time() - retain_days * 86400) * 1000)
    cur = db.execute("DELETE FROM usage WHERE ts IS NOT NULL AND ts < ?", (cutoff,))
    db.commit()
    return cur.rowcount


# ---- 기록 탭: 하루 단위 묶음 ------------------------------------------------
_DAY = "date(u.ts / 1000 + 32400, 'unixepoch')"     # 한국 시각 자정 기준


def _kst_day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000 + 9 * 3600))


def _kst_midnight_ms(day):
    import calendar
    return (calendar.timegm(time.strptime(day, "%Y-%m-%d")) - 9 * 3600) * 1000


def refresh_daily(db, retain_days=30, now_ms=None, long_context_over=272_000):
    """usage 가 아직 다 갖고 있는 날들을 다시 묶는다. prune **전에** 불러야 한다.

    prune 은 지금부터 retain_days 전보다 오래된 턴을 지운다. 그 경계가 걸린 날은
    이미 앞부분이 지워졌을 수 있으므로 그 다음 날부터만 다시 묶는다. 그보다 앞선
    날은 예전에 묶어 둔 값을 그대로 둔다 — 그게 턴이 지워진 뒤에도 남는 기록이다.
    중앙에서 옮겨 온 구간(history_local_from 이전)은 건드리지 않는다.
    """
    now_ms = now_ms or int(time.time() * 1000)
    start = _kst_day(now_ms - retain_days * 86400 * 1000 + 86400 * 1000) if retain_days else "0000-00-00"
    local_from = meta_get(db, "history_local_from")
    if local_from and local_from > start:
        start = local_from
    start_ms = _kst_midnight_ms(start) if start[0] != "0" else 0
    db.execute("DELETE FROM usage_daily WHERE src = 'local' AND day >= ?", (start,))
    db.execute(f"""
        INSERT INTO usage_daily
        SELECT {_DAY}, 'local', u.provider, COALESCE(u.account_email, ''),
               COALESCE(u.host, ''), COALESCE(u.session_id, ''), COALESCE(st.cwd, ''),
               COALESCE(u.model, ''), COALESCE(NULLIF(u.speed, ''), 'standard'),
               COALESCE(u.effort, ''),
               CASE WHEN u.provider = 'codex' AND COALESCE(u.input, 0) + COALESCE(u.cache_read, 0)
                         + COALESCE(u.cache_creation, 0) > ? THEN 1 ELSE 0 END,
               COUNT(*), SUM(COALESCE(u.input, 0)), SUM(COALESCE(u.output, 0)),
               SUM(COALESCE(u.cache_creation, 0)), SUM(COALESCE(u.cache_read, 0)),
               SUM(COALESCE(u.cache_creation_1h, 0)),
               SUM(CASE WHEN u.cache_creation_1h IS NULL THEN COALESCE(u.cache_creation, 0) ELSE 0 END)
        FROM usage u
        LEFT JOIN session_totals st ON st.provider = u.provider AND st.session_id = u.session_id
        WHERE u.ts >= ?
        GROUP BY 1, 3, 4, 5, 6, 7, 8, 9, 10, 11""", (long_context_over, start_ms))
    db.commit()
    return start


def import_daily(db, records, until_day):
    """이 DB 가 생기기 전 기간을 다른 집계(중앙 서버)에서 한 번 옮겨 온다.

    until_day 이전 날짜만 'central' 로 넣고, 그 날부터는 이 DB 의 것을 쓰도록
    경계를 남긴다. 다시 부르면 'central' 몫을 통째로 갈아 끼운다.
    """
    db.execute("DELETE FROM usage_daily WHERE src = 'central'")
    n = 0
    for r in records:
        if r["day"] >= until_day:
            continue
        db.execute("""INSERT OR REPLACE INTO usage_daily VALUES
            (?, 'central', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            r["day"], r["provider"], r.get("account_email") or "", r.get("host") or "",
            r.get("session_id") or "", r.get("cwd") or "", r.get("model") or "",
            r.get("speed") or "standard", r.get("effort") or "", int(r.get("lc") or 0),
            r.get("n") or 0, r.get("i") or 0, r.get("o") or 0, r.get("cw") or 0,
            r.get("cr") or 0, r.get("cw1h") or 0, r.get("cwu") or 0))
        n += 1
    meta_set(db, "history_local_from", until_day)
    db.commit()
    return n


def daily_records(db):
    """기록 탭에 넘길 레코드. 날마다 출처는 하나 — 중앙 구간과 이 DB 구간이 겹치지 않는다."""
    local_from = meta_get(db, "history_local_from")
    if local_from:
        where, args = "(src = 'central' AND day < ?) OR (src = 'local' AND day >= ?)", (local_from, local_from)
    else:
        where, args = "src = 'local'", ()
    for r in db.execute(f"SELECT * FROM usage_daily WHERE {where}", args):
        yield dict(r)
