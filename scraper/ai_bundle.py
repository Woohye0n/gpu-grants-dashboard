"""모아 둔 기록에서 화면이 읽는 스냅샷(dashboard.json)을 만든다.

`docs/ai/app.js` 는 스냅샷 하나만 읽습니다. 그 스냅샷을 예전에는 다른 사람의 중앙
서버가 만들어 다른 깃헙 저장소로 발행했고, 그게 멈추자 화면도 같이 멈췄습니다.
여기서는 같은 모양을 이 저장소가 직접 만듭니다 — 입력은 NAS inbox 뿐입니다.

화면이 실제로 읽는 필드만 채웁니다(`timeseries`·`pricing_models` 는 앱이 쓰지
않아 비워 둡니다). 남는 필드를 흉내내는 것보다, 채운 것이 전부 근거 있는 값인
편이 낫습니다.
"""
from __future__ import annotations

import fnmatch
import json
import socket
import time
from datetime import datetime, timezone

WINDOWS = {"5h": 5 * 3600 * 1000, "7d": 7 * 86400 * 1000}
METRIC_KEYS = ("input", "output", "cache_creation", "cache_read",
               "io", "billable", "total", "messages")


def metrics(i=0, o=0, cw=0, cr=0, messages=0):
    i, o, cw, cr = int(i or 0), int(o or 0), int(cw or 0), int(cr or 0)
    return {"input": i, "output": o, "cache_creation": cw, "cache_read": cr,
            "io": i + o, "billable": i + o + cw, "total": i + o + cw + cr,
            "messages": int(messages or 0)}


def _add(a, b):
    return metrics(a["input"] + b["input"], a["output"] + b["output"],
                   a["cache_creation"] + b["cache_creation"],
                   a["cache_read"] + b["cache_read"], a["messages"] + b["messages"])


EMPTY = metrics()


def _iso_ms(iso):
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def window_start(rate_limits, key, now):
    """창의 시작점 — 지금으로부터 거꾸로 세는 롤링 창.

    한도 리셋 시각(`resets_at`)에 맞춰 세는 것도 생각했지만, 같은 데이터로
    중앙 백엔드와 맞춰 보니 중앙이 롤링으로 셉니다(claude 랩계정 5시간: 중앙 404건
    ≈ 롤링 392건, 리셋 정렬 29건). 게다가 `resets_at` 은 사용량 API 가 인증에
    실패하면 며칠씩 낡은 채로 남아 있어(지금 실제로 그렇습니다) 그걸 기준으로
    창을 자르면 조용히 대부분을 버립니다. `resets_at` 은 게이지 표시에만 씁니다.
    """
    return now - WINDOWS[key]


def owner_of(rules, session):
    """사람을 가르는 유일한 근거는 작업 디렉토리다(필요하면 호스트·도구로 좁힌다)."""
    for r in rules or []:
        if r.get("host") and r["host"] != session.get("host"):
            continue
        if r.get("provider") and r["provider"] != session.get("provider"):
            continue
        pattern = r.get("cwd_glob")
        if pattern and not fnmatch.fnmatch(session.get("cwd") or "", pattern):
            continue
        if not (r.get("host") or r.get("provider") or pattern):
            continue
        return r.get("owner")
    return None


def _account_status(email, provider, usage_status, overrides):
    forced = (overrides or {}).get(f"{provider}:{email}") or (overrides or {}).get(email)
    if forced:
        return forced
    if usage_status in ("unauthorized", "auth_error"):
        return "auth_error"
    return "ok"


def _thresholds(cfg_alerts, account, rate_limits):
    out = []
    for t in (cfg_alerts or {}).get("thresholds") or []:
        if t.get("provider") and t["provider"] != account["provider"]:
            continue
        acct = t.get("account") or "*"
        if acct != "*" and acct != account["email"]:
            continue
        win = t.get("window")
        data = (rate_limits or {}).get(win) or {}
        value = data.get("utilization")
        limit = t.get("limit")
        out.append({
            "name": t.get("name") or win, "window": win,
            "metric": t.get("metric") or "utilization", "limit": limit,
            "value": value, "resets_at": data.get("resets_at"),
            "available": value is not None,
            "breached": bool(value is not None and limit is not None and value >= limit),
            "cooldown_minutes": t.get("cooldown_minutes",
                                      (cfg_alerts or {}).get("default_cooldown_minutes", 30)),
        })
    return out


def _record_alerts(db, accounts, now):
    """한도를 넘은 순간을 남긴다 — 화면의 '알림' 탭이 읽는 이력이다."""
    for a in accounts:
        if a.get("status") == "suspended" or a.get("usage_stale"):
            continue                       # 얼어붙은 퍼센트로 알림을 울리지 않는다
        for t in a.get("thresholds") or []:
            if not t.get("breached"):
                continue
            cooldown = (t.get("cooldown_minutes") or 30) * 60 * 1000
            row = db.execute(
                "SELECT ts FROM alerts WHERE name=? AND account=? ORDER BY ts DESC LIMIT 1",
                (t["name"], a["email"])).fetchone()
            if row and now - (row["ts"] or 0) < cooldown:
                continue
            label = {"five_hour": "5시간", "seven_day": "주간(전체)"}.get(t["window"], t["window"])
            db.execute("""INSERT INTO alerts(ts, name, account, provider, window, metric,
                            value, limit_value, message) VALUES(?,?,?,?,?,?,?,?,?)""",
                       (now, t["name"], a["email"], a["provider"], t["window"],
                        t["metric"], t["value"], t["limit"],
                        f"[AIDAS 사용량 알림] {a['email']} · {label} "
                        f"{t['value']:.0f}% (한도 {t['limit']}% 도달)"))
    db.commit()


def build(db, cfg, now=None):
    now = int(now or time.time() * 1000)
    tracking = cfg.get("tracking") or {}
    allowed = set(tracking.get("allowed_accounts") or [])
    count_from = int(tracking.get("count_from_ms") or 0)
    live_seconds = int((cfg.get("collect") or {}).get("session_live_seconds") or 360)
    # 노드가 '살아 있다' 고 볼 시간. 송신기는 5분마다 보고하므로 6분(=기존
    # session_live_seconds)으로 재면 한 번만 늦어도 그 노드 세션이 통째로 죽는다.
    # 실제로 6.4분 전에 보고한 노드의 세션이 전부 꺼져 보였다.
    node_seconds = int((cfg.get("collect") or {}).get("node_stale_seconds") or 900)
    # 프로세스가 살아 있어도 이만큼 쉬었으면 '지금 떠 있는' 축에 넣지 않는다.
    idle_seconds = int((cfg.get("collect") or {}).get("session_idle_seconds") or 3600)
    rules = (cfg.get("people") or {}).get("rules") or []

    nodes, node_last = [], {}
    for r in db.execute("SELECT * FROM nodes ORDER BY host"):
        nodes.append({"host": r["host"], "os_user": r["os_user"],
                      "sender_root": r["sender_root"], "fqdn": r["fqdn"],
                      "ip": r["ip"], "machine_id": r["machine_id"],
                      "last_report": r["last_report"]})
        node_last[r["host"]] = r["last_report"] or 0

    # ---- 계정 --------------------------------------------------------------
    accounts, acct_window = [], {}
    rows = list(db.execute("SELECT * FROM accounts ORDER BY provider, email"))
    # allowlist 에 있는데 아직 아무도 보고하지 않은 계정도 카드로 남긴다. 안 그러면
    # '정지된 계정' 이 화면에서 통째로 사라져, 정지인지 미사용인지 알 수 없다.
    known = {(r["provider"], r["email"]) for r in rows}
    for email in sorted(allowed):
        for provider in ("claude", "codex"):
            if (provider, email) not in known and \
                    (tracking.get("account_status") or {}).get(f"{provider}:{email}"):
                rows.append({"provider": provider, "email": email, "account_id": None,
                             "org_type": None, "rate_limit_tier": None,
                             "display_name": None, "org_name": None, "rate_limits": None,
                             "rate_limits_updated_at": None, "usage_status": None,
                             "usage_status_at": None, "last_seen": None})
    for r in rows:
        email, provider = r["email"], r["provider"]
        if allowed and email not in allowed:
            continue
        try:
            rl = json.loads(r["rate_limits"]) if r["rate_limits"] else None
        except ValueError:
            rl = None
        wins = {}
        for key in WINDOWS:
            start = max(window_start(rl, key, now), count_from)
            row = db.execute(
                """SELECT COUNT(*) n, SUM(input) i, SUM(output) o,
                          SUM(cache_creation) cw, SUM(cache_read) cr
                   FROM usage WHERE account_email=? AND provider=? AND ts>=?""",
                (email, provider, start)).fetchone()
            wins[key] = metrics(row["i"], row["o"], row["cw"], row["cr"], row["n"])
            acct_window[(provider, email, key)] = wins[key]["total"]
        life = db.execute(
            """SELECT SUM(messages) n, SUM(input) i, SUM(output) o,
                      SUM(cache_creation) cw, SUM(cache_read) cr
               FROM session_totals WHERE account_email=? AND provider=?""",
            (email, provider)).fetchone()
        hosts = [x["host"] for x in db.execute(
            "SELECT DISTINCT host FROM session_totals WHERE account_email=? AND provider=? "
            "AND host IS NOT NULL", (email, provider))]
        live_hosts = [x["host"] for x in db.execute(
            """SELECT DISTINCT host FROM session_meta WHERE account_email=? AND provider=?
               AND host IS NOT NULL AND COALESCE(updated_at,0) >= ?""",
            (email, provider, now - live_seconds * 1000))]
        status = _account_status(email, provider, r["usage_status"],
                                 tracking.get("account_status"))
        updated = r["rate_limits_updated_at"] or 0
        account = {
            "email": email, "provider": provider, "account_id": r["account_id"],
            "org_type": r["org_type"], "rate_limit_tier": r["rate_limit_tier"],
            "display_name": r["display_name"], "org_name": r["org_name"],
            "last_seen": r["last_seen"], "status": status,
            "usage_status": r["usage_status"], "usage_status_at": r["usage_status_at"],
            "usage_updated_at": updated or None,
            # 오래된 퍼센트를 현재값처럼 보여주지 않는다 — 화면이 이 값으로
            # 게이지를 흐리게 하고 "스테일" 이라고 적는다.
            "usage_stale": bool(updated and now - updated > 60 * 60 * 1000),
            "rate_limits": rl, "windows": wins,
            "lifetime": metrics(life["i"], life["o"], life["cw"], life["cr"], life["n"]),
            "hosts": sorted(hosts), "live_hosts": sorted(live_hosts),
        }
        account["thresholds"] = _thresholds(cfg.get("alerts"), account, rl)
        accounts.append(account)
    _record_alerts(db, accounts, now)

    # ---- 세션 --------------------------------------------------------------
    sessions, live_count = [], 0
    meta = {(r["provider"], r["session_id"]): r
            for r in db.execute("SELECT * FROM session_meta")}
    for r in db.execute("SELECT * FROM session_totals ORDER BY last_ts DESC"):
        key = (r["provider"], r["session_id"])
        m = meta.get(key)
        host = r["host"] or (m["host"] if m else None)
        email = r["account_email"] or (m["account_email"] if m else None)
        if allowed and email not in allowed:
            continue
        updated = max(r["last_ts"] or 0, (m["updated_at"] if m else 0) or 0)
        # 라이브 판정에는 세 가지가 다 필요하다. 하나만 쓰면 어느 쪽으로든 틀린다.
        #   최근 활동만  → 10분 쉬고 있던 살아 있는 세션이 꺼진 것으로 나온다
        #   pid 생존만   → 19일째 떠 있기만 한 세션이 계속 '지금 떠 있는' 것으로 남는다
        #   노드 보고    → 없으면 송신기가 죽은 노드의 세션이 영원히 살아 있다
        # 그래서 "프로세스가 살아 있고, 그 노드가 최근 보고했고, 너무 오래 쉬지
        # 않았다" 로 본다. pid 를 안 주는 쪽(codex)은 최근 활동만으로 판정한다.
        node_fresh = (now - node_last.get(host, 0)) <= node_seconds * 1000
        pid_alive = m["pid_alive"] if m else None
        idle = now - updated
        if pid_alive == 1:
            live = bool(node_fresh and idle <= idle_seconds * 1000)
        else:
            live = bool(node_fresh and pid_alive is None and idle <= live_seconds * 1000)
        live_count += 1 if live else 0
        row = {
            "session_id": r["session_id"], "provider": r["provider"],
            "account_email": email, "host": host, "cwd": r["cwd"] or (m["cwd"] if m else None),
            "project": r["project"] or (m["project"] if m else None),
            "first_ts": r["first_ts"], "last_ts": r["last_ts"],
            "messages": r["messages"],
            "models": json.loads(r["models"] or "[]"),
            "surface": (m["surface"] if m else None) or
                       (json.loads(r["surfaces"] or "[]") or [None])[0],
            "metrics": metrics(r["input"], r["output"], r["cache_creation"], r["cache_read"]),
            "status": (m["status"] if m else None) or "active",
            "live": live, "pid": m["pid"] if m else None, "pid_alive": pid_alive,
            "started_at": r["first_ts"] or (m["started_at"] if m else None),
            "updated_at": updated, "version": m["version"] if m else None,
            "owner": None,
        }
        row["owner"] = owner_of(rules, row)
        for key_w in WINDOWS:
            start = now - WINDOWS[key_w]
            got = db.execute(
                "SELECT SUM(input+output+cache_creation+cache_read) t FROM usage "
                "WHERE session_id=? AND provider=? AND ts>=?",
                (r["session_id"], r["provider"], start)).fetchone()["t"] or 0
            row[f"tokens_{key_w}"] = got
            total = acct_window.get((r["provider"], email, key_w)) or 0
            util = ((rl_of(accounts, r["provider"], email) or {})
                    .get({"5h": "five_hour", "7d": "seven_day"}[key_w]) or {}).get("utilization")
            # "이 세션이 한도의 몇 %를 썼나" — 계정 소진율을 세션 비중으로 나눈 추정.
            row[f"share_{key_w}"] = (util * got / total) if (util and total) else None
        sessions.append(row)

    # ---- 사람 --------------------------------------------------------------
    people = {}
    for s in sessions:
        owner = s["owner"] or "미분류"
        p = people.setdefault(owner, {
            "owner": owner, "lifetime": EMPTY, "live_sessions": 0,
            "windows": {w: EMPTY for w in WINDOWS},
            "breakdown": {w: {} for w in WINDOWS},
            "providers": set(), "hosts": set(), "accounts": set()})
        p["lifetime"] = _add(p["lifetime"], metrics(
            s["metrics"]["input"], s["metrics"]["output"],
            s["metrics"]["cache_creation"], s["metrics"]["cache_read"], s["messages"]))
        p["live_sessions"] += 1 if s["live"] else 0
        p["providers"].add(s["provider"])
        if s["host"]:
            p["hosts"].add(s["host"])
        if s["account_email"]:
            p["accounts"].add(s["account_email"])
    for w in WINDOWS:
        start = now - WINDOWS[w]
        for r in db.execute(
                """SELECT u.provider, u.account_email, u.session_id,
                          COUNT(*) n, SUM(u.input) i, SUM(u.output) o,
                          SUM(u.cache_creation) cw, SUM(u.cache_read) cr
                   FROM usage u WHERE u.ts>=? GROUP BY u.provider, u.account_email, u.session_id""",
                (start,)):
            if allowed and r["account_email"] not in allowed:
                continue
            sess = next((s for s in sessions if s["session_id"] == r["session_id"]
                         and s["provider"] == r["provider"]), None)
            owner = (sess or {}).get("owner") or "미분류"
            p = people.setdefault(owner, {
                "owner": owner, "lifetime": EMPTY, "live_sessions": 0,
                "windows": {x: EMPTY for x in WINDOWS},
                "breakdown": {x: {} for x in WINDOWS},
                "providers": set(), "hosts": set(), "accounts": set()})
            m = metrics(r["i"], r["o"], r["cw"], r["cr"], r["n"])
            p["windows"][w] = _add(p["windows"][w], m)
            bkey = (r["provider"], r["account_email"])
            p["breakdown"][w][bkey] = _add(p["breakdown"][w].get(bkey, EMPTY), m)

    people_out = []
    for p in people.values():
        people_out.append({
            "owner": p["owner"], "lifetime": p["lifetime"], "windows": p["windows"],
            "live_sessions": p["live_sessions"],
            "providers": sorted(p["providers"]), "hosts": sorted(p["hosts"]),
            "accounts": sorted(p["accounts"]),
            "breakdown": {w: [dict(provider=k[0], account_email=k[1], **v)
                              for k, v in sorted(p["breakdown"][w].items())]
                          for w in WINDOWS},
        })
    people_out.sort(key=lambda x: -x["windows"]["7d"]["total"])

    totals = {w: EMPTY for w in WINDOWS}
    for a in accounts:
        for w in WINDOWS:
            totals[w] = _add(totals[w], a["windows"][w])

    alerts = [dict(r) for r in db.execute(
        "SELECT id, ts, name, account, provider, window, metric, value, limit_value, message "
        "FROM alerts ORDER BY ts DESC LIMIT 50")]

    since = db.execute("SELECT MIN(ts) t FROM usage").fetchone()["t"]
    return {
        "schema": 1, "generated_at": now,
        "summary": {
            "now": now, "host": socket.gethostname(), "headline_metric": "total",
            "windows": list(WINDOWS), "accounts": accounts, "nodes": nodes,
            "people": people_out, "people_policy": "full",
            "totals": totals, "live_session_count": live_count,
            "notifiers": ["file"], "email_enabled": bool((cfg.get("email") or {}).get("enabled")),
            "collect": cfg.get("collect") or {}, "nas": cfg.get("nas") or {},
            # 창이 아직 덜 찼는지 화면/사람이 알 수 있도록.
            "data_since": since, "pricing_models": [],
        },
        "sessions": sessions, "alerts": alerts, "timeseries": {},
        "config": {k: cfg.get(k) for k in ("tracking", "alerts", "email", "collect", "nas")
                   if cfg.get(k) is not None},
    }


def rl_of(accounts, provider, email):
    for a in accounts:
        if a["provider"] == provider and a["email"] == email:
            return a.get("rate_limits")
    return None
