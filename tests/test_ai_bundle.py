#!/usr/bin/env python3
"""AI 사용량 집계의 판정 규칙을 고정한다 (표준 라이브러리만).

    python3 tests/test_ai_bundle.py

특히 '라이브 세션' 판정은 세 번 틀렸다. 한 번은 최근 활동만 봐서 10분 쉬던 살아
있는 세션을 꺼뜨렸고, 한 번은 pid 만 봐서 19일째 떠 있기만 한 세션을 켜뒀고,
한 번은 노드 판정창이 송신 주기보다 짧아 6.4분 전에 보고한 노드의 세션을 통째로
꺼뜨렸다. 셋 다 화면에서는 '그냥 데이터가 없네' 로만 보인다.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scraper import ai_bundle, ai_store          # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        기대 {want!r} / 실제 {got!r}")
        fails.append(name)


NOW = int(time.time() * 1000)
MIN = 60 * 1000


def bundle(sessions, node_age_min=1.0, cfg_extra=None):
    """메모리 DB 에 세션을 넣고 스냅샷을 만든다."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(ai_store.SCHEMA)
    db.execute("INSERT INTO nodes(host, last_report) VALUES('n1', ?)",
               (NOW - int(node_age_min * MIN),))
    for i, (pid_alive, idle_min) in enumerate(sessions):
        sid = f"s{i}"
        db.execute("""INSERT INTO session_totals(provider, session_id, account_email, host,
                        cwd, first_ts, last_ts, messages, input, output, cache_creation,
                        cache_read, models, surfaces)
                      VALUES('claude',?,'lab@x','n1','/w/woohyeon/p',?,?,1,1,1,0,0,'[]','[]')""",
                   (sid, NOW - int(idle_min * MIN), NOW - int(idle_min * MIN)))
        db.execute("""INSERT INTO session_meta(provider, session_id, host, account_email,
                        cwd, status, pid, pid_alive, updated_at)
                      VALUES('claude',?,'n1','lab@x','/w/woohyeon/p','active',1,?,?)""",
                   (sid, pid_alive, NOW - int(idle_min * MIN)))
    cfg = {"tracking": {"allowed_accounts": ["lab@x"]},
           "people": {"rules": [{"owner": "woohyeon", "cwd_glob": "*/woohyeon*"}]},
           "collect": dict(cfg_extra or {})}
    return ai_bundle.build(db, cfg, now=NOW)


print("[1] 라이브 판정")
# pid 가 살아 있고 최근 활동 → 당연히 라이브
check("pid 살아 있고 방금 활동", bundle([(1, 1)])["summary"]["live_session_count"], 1)
# 10분 쉬어도 프로세스가 살아 있으면 라이브 (여기서 틀렸었다)
check("pid 살아 있고 10분 쉼", bundle([(1, 10)])["summary"]["live_session_count"], 1)
# 하루 넘게 쉰 건 '지금 떠 있는' 축에 넣지 않는다 (여기서도 틀렸었다)
check("pid 살아 있어도 하루 쉼", bundle([(1, 60 * 25)])["summary"]["live_session_count"], 0)
# 프로세스가 죽었으면 방금 활동해도 아니다
check("pid 죽음", bundle([(0, 1)])["summary"]["live_session_count"], 0)
# pid 를 모르는 쪽(codex)은 최근 활동으로만 본다
check("pid 모름 + 방금", bundle([(None, 1)])["summary"]["live_session_count"], 1)
check("pid 모름 + 10분 쉼", bundle([(None, 10)])["summary"]["live_session_count"], 0)

print("\n[2] 노드 보고 시차")
# 송신기는 5분마다 보고한다. 판정창이 6분이면 한 번만 늦어도 전부 꺼진다.
check("노드 6.4분 전 보고 (5분 주기의 정상 범위)",
      bundle([(1, 1)], node_age_min=6.4)["summary"]["live_session_count"], 1)
check("노드 20분째 조용 → 그 노드 세션은 라이브 아님",
      bundle([(1, 1)], node_age_min=20)["summary"]["live_session_count"], 0)

print("\n[3] 사람 귀속과 창")
b = bundle([(1, 1)])
check("cwd 로 사람이 붙는다", b["sessions"][0]["owner"], "woohyeon")
check("사람 카드가 생긴다", [p["owner"] for p in b["summary"]["people"]], ["woohyeon"])
check("창은 롤링이다 (resets_at 없이도 집계)",
      ai_bundle.window_start(None, "5h", NOW), NOW - ai_bundle.WINDOWS["5h"])

print("\n[4] 토큰 파생값")
m = ai_bundle.metrics(10, 5, 2, 3, 1)
check("io = 입력+출력", m["io"], 15)
check("billable = io + 캐시생성", m["billable"], 17)
check("total = 전부", m["total"], 20)

print()


# ---- 구버전 송신기가 보낸 표면 이름 보정 ----------------------------------
from scraper.ai_store import _surface

check("구버전 codex-tui 를 터미널로", _surface("other:codex-tui"), "terminal")
check("모르는 표면은 그대로 둔다",
      _surface("other:codex_chatgpt_android_remote"), "other:codex_chatgpt_android_remote")
check("이미 정상인 값은 건드리지 않는다", _surface("vscode"), "vscode")
check("값이 없으면 그대로", _surface(None), None)



# ---- 크레딧: 토큰 종류·모델·속도별 공식 단가 --------------------------------
# 1 크레딧 = $10. 기대값은 전부 공식 단가표에서 손으로 계산한 값이다.
from scraper import ai_pricing                    # noqa: E402


def credit_bundle(rows):
    """턴 행(rows)만으로 사람 한 명의 스냅샷을 만든다."""
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(ai_store.SCHEMA)
    db.execute("INSERT INTO nodes(host, last_report) VALUES('n1', ?)", (NOW,))
    sids = set()
    for i, r in enumerate(rows):
        r = dict({"provider": "claude", "session_id": "s1", "speed": None, "effort": None,
                  "input": 0, "output": 0, "cache_creation": 0, "cache_read": 0,
                  "cache_creation_1h": None}, **r)
        sids.add((r["provider"], r["session_id"]))
        db.execute("""INSERT INTO usage(uuid, ts, provider, host, account_email, session_id,
                        model, input, output, cache_creation, cache_read,
                        speed, effort, cache_creation_1h)
                      VALUES(?,?,?,'n1','lab@x',?,?,?,?,?,?,?,?,?)""",
                   (f"u{i}", NOW - MIN, r["provider"], r["session_id"], r["model"],
                    r["input"], r["output"], r["cache_creation"], r["cache_read"],
                    r["speed"], r["effort"], r["cache_creation_1h"]))
    for prov, sid in sids:
        db.execute("""INSERT INTO session_totals(provider, session_id, account_email, host,
                        cwd, first_ts, last_ts, messages, models, surfaces)
                      VALUES(?,?,'lab@x','n1','/w/woohyeon/p',?,?,1,'[]','[]')""",
                   (prov, sid, NOW - MIN, NOW - MIN))
    cfg = {"tracking": {"allowed_accounts": ["lab@x"]},
           "people": {"rules": [{"owner": "woohyeon", "cwd_glob": "*/woohyeon*"}]}}
    p = ai_bundle.build(db, cfg, now=NOW)["summary"]["people"][0]
    return p["credits"]["5h"], p["credit_detail"]["5h"]


def near(a, b):
    return abs(a - b) < 1e-9


M = 1_000_000
print("\n[5] 크레딧 — 공식 단가대로 매기는가")
c, _ = credit_bundle([{"model": "claude-opus-5-5", "input": M}])
check("Opus 5.5 입력 1M = $4 = 0.4 크레딧", near(c, 0.4), True)
c, _ = credit_bundle([{"model": "claude-opus-5-5", "output": M}])
check("Opus 5.5 출력 1M = $20 = 2 크레딧", near(c, 2.0), True)
c, _ = credit_bundle([{"model": "claude-opus-5-5", "cache_read": M}])
check("Opus 5.5 캐시 읽기 1M = $0.20 (입력가 x0.05)", near(c, 0.02), True)
c, _ = credit_bundle([{"model": "claude-opus-5-5", "cache_creation": M, "cache_creation_1h": 0}])
check("5분 캐시 쓰기 1M = $5 (입력가 x1.25)", near(c, 0.5), True)
c, _ = credit_bundle([{"model": "claude-opus-5-5", "cache_creation": M, "cache_creation_1h": M}])
check("1시간 캐시 쓰기 1M = $8 (입력가 x2) — 5분과 다르다", near(c, 0.8), True)
c, d = credit_bundle([{"model": "claude-opus-5-5", "cache_creation": M}])
check("TTL 을 모르는 쓰기는 1시간 단가로, 따로 표시", (near(c, 0.8),
      [x["component"] for x in d["components"]]), (True, ["cache_write_ttl_unknown"]))
c, _ = credit_bundle([{"model": "claude-opus-5-5", "output": M, "speed": "fast"}])
check("fast mode 는 2배 ($40)", near(c, 4.0), True)
c, _ = credit_bundle([{"model": "claude-opus-4-6", "output": M, "speed": "fast"}])
check("Opus 4.6 은 fast 를 받아도 표준가", near(c, 2.5), True)
c, _ = credit_bundle([{"model": "claude-fable-5", "cache_read": M}])
check("모델이 다르면 캐시 읽기 배수도 다르다 (Fable 5 $1)", near(c, 0.1), True)
c, _ = credit_bundle([{"model": "claude-opus-5-5-20260401", "input": M}])
check("날짜가 붙은 모델 ID 도 찾는다", near(c, 0.4), True)

# 한 턴에 1M 을 넣으면 그 자체로 장문맥(>272K)이라 표준가 확인은 272K 아래로 한다.
c, _ = credit_bundle([{"provider": "codex", "model": "gpt-5.6-sol", "input": 200_000}])
check("gpt-5.6-sol 입력 200K = $0.80 (표준가 $4/1M)", near(c, 0.08), True)
c, _ = credit_bundle([{"provider": "codex", "model": "gpt-5.6-sol", "input": M}])
check("같은 1M 이라도 한 요청이면 장문맥 ($8/1M)", near(c, 0.8), True)
c, _ = credit_bundle([{"provider": "codex", "model": "gpt-5.6-sol", "output": M,
                       "speed": "fast"}])
check("Codex Fast(=priority) 는 2배 ($40)", near(c, 4.0), True)
c, _ = credit_bundle([{"provider": "codex", "model": "gpt-5.6-sol", "output": M,
                       "speed": "flex"}])
check("Codex Flex 는 절반 ($10)", near(c, 1.0), True)
c, _ = credit_bundle([{"provider": "codex", "model": "gpt-6-astra",
                       "input": 100_000, "cache_read": 200_000, "output": 1000}])
# 요청 하나의 입력 300K > 272K → 입력·캐시 x2, 출력 x1.5
want = (100_000 * 20 + 200_000 * 2 + 1000 * 75) / M / 10
check("OpenAI 장문맥(>272K) 할증은 요청 단위로", near(c, want), True)
c, _ = credit_bundle([{"provider": "codex", "model": "gpt-6-astra",
                       "input": 100_000, "cache_read": 100_000, "output": 1000}])
want = (100_000 * 10 + 100_000 * 1 + 1000 * 50) / M / 10
check("272K 이하는 표준가", near(c, want), True)

c, d = credit_bundle([{"model": "claude-opus-5-5", "input": M},
                      {"provider": "codex", "model": "gpt-reserve", "input": 5000,
                       "session_id": "s2"}])
check("모르는 모델은 0 으로 숨기지 않고 따로 드러낸다",
      (near(c, 0.4), [(u["model"], u["tokens"]) for u in d["unpriced"]]),
      (True, [("gpt-reserve", 5000)]))
c, d = credit_bundle([{"provider": "codex", "model": "gpt-6-astra", "input": M,
                       "speed": "ultrafast"}])
check("모르는 처리 티어도 마찬가지", (c, d["unpriced"][0]["speed"]), (0.0, "ultrafast"))
c, d = credit_bundle([{"model": "claude-opus-5-5", "input": M},
                      {"provider": "codex", "model": "codex-auto-review", "input": 5000,
                       "cache_read": 90_000, "output": 85, "session_id": "s2"}])
ar = [g for g in d["groups"] if g["model"] == "codex-auto-review"]
check("codex-auto-review 는 단가 ≈ 0 — 크레딧에 안 들어가고 미등록도 아니다",
      (near(c, 0.4), d["unpriced"], ar[0]["credits"], ar[0]["tokens"]), (True, [], 0.0, 95_085))
check("그 묶음에 정체를 밝히는 note 가 붙는다", bool(ar[0]["note"]), True)
check("토큰 종류별 합계에는 섞지 않는다 (입력 = Opus 의 1M 만)",
      [x["tokens"] for x in d["components"] if x["component"] == "input"], [M])
c, d = credit_bundle([{"provider": "codex", "model": "gpt-5.5", "input": 1000,
                       "cache_creation": 500}])
check("단가가 없는 토큰 종류만 따로 빼고 나머지는 계산한다",
      (near(c, 1000 * 5 / M / 10), d["unpriced"][0]["tokens"]), (True, 500))
c, d = credit_bundle([{"provider": "codex", "model": "gpt-6-sol", "input": 300_000}])
check("장문맥 단가를 모르는 모델은 표준가로 짐작하지 않는다",
      (c, d["unpriced"][0]["reason"]), (0.0, "장문맥(>272K) 단가 미확인"))
c, d = credit_bundle([{"model": "claude-opus-5-5", "input": 300_000},
                      {"model": "claude-opus-5-5", "input": 1000}])
check("Claude 는 272K 를 넘어도 표준가 (장문맥 할증 없음)",
      near(c, 301_000 * 4 / M / 10), True)
check("Claude 는 장문맥으로 묶음을 쪼개지 않는다",
      [(g["model"], g["long_context"]) for g in d["groups"]], [("claude-opus-5-5", False)])
c, _ = credit_bundle([{"provider": "codex", "model": "gpt-6.1-sol", "cache_read": 200_000}])
check("gpt-6.1-sol 캐시 읽기 $0.10/1M (모델마다 다르다)", near(c, 200_000 * 0.10 / M / 10), True)

c, d = credit_bundle([
    {"model": "claude-opus-5-5", "input": 1000, "output": 2000, "cache_read": M,
     "cache_creation": 50_000, "cache_creation_1h": 50_000, "effort": "xhigh"},
    {"model": "claude-opus-5-5", "output": 3000, "effort": "low", "session_id": "s2"},
    {"provider": "codex", "model": "gpt-5.6-sol", "input": 4000, "output": 100,
     "cache_read": 90_000, "speed": "fast", "session_id": "s3"}])
check("묶음 비중의 합 = 100%", round(sum(g["pct"] for g in d["groups"]), 1), 100.0)
check("토큰 종류 비중의 합 = 100%", round(sum(x["pct"] for x in d["components"]), 1), 100.0)
check("effort 별로 따로 묶는다",
      sorted(g["effort"] for g in d["groups"] if g["provider"] == "claude"), ["low", "xhigh"])
check("묶음 크레딧의 합 = 그 사람의 창 크레딧",
      near(round(sum(g["credits"] for g in d["groups"]), 4), round(c, 4)), True)


# ---- 저장: 옛 DB 이어받기, codex 턴 정체성 ----------------------------------
import tempfile                                    # noqa: E402

print("\n[6] 저장 — 옛 DB 를 이어받고, 같은 턴을 두 번 세지 않는가")
tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "old.db")
old = sqlite3.connect(path)
old.execute("""CREATE TABLE usage (uuid TEXT PRIMARY KEY, ts INTEGER, provider TEXT,
                 host TEXT, account_email TEXT, session_id TEXT, model TEXT, surface TEXT,
                 input INTEGER, output INTEGER, cache_creation INTEGER, cache_read INTEGER)""")
# 같은 codex 턴이 resume 로 재개 시각을 달고 두 번 들어와 있던 상태
for uid, ts in (("codex:S:100:1", 100), ("codex:S:900:7", 900)):
    old.execute("INSERT INTO usage VALUES(?,?,'codex','n','lab@x','S','gpt-5.6-sol',"
                "NULL,10,2,0,30)", (uid, ts))
old.execute("INSERT INTO usage VALUES('c1',5,'claude','n','lab@x','C','claude-opus-5',"
            "NULL,1,1,0,0)")
old.commit()
old.close()
db = ai_store.connect(path)
cols = {r[1] for r in db.execute("PRAGMA table_info(usage)")}
check("옛 DB 에 새 컬럼이 붙는다",
      {"speed", "effort", "cache_creation_1h"} <= cols, True)
rows = db.execute("SELECT uuid, ts FROM usage WHERE provider='codex'").fetchall()
check("재개로 겹친 codex 턴이 하나로 접힌다", len(rows), 1)
check("남는 것은 원래 시각", rows[0]["ts"], 100)
check("키가 내용 기반으로 바뀐다", rows[0]["uuid"],
      ai_store.codex_usage_uuid("S", "gpt-5.6-sol", 10, 2, 30))
check("claude 행은 건드리지 않는다",
      db.execute("SELECT uuid FROM usage WHERE provider='claude'").fetchone()[0], "c1")
ai_store.connect(path).close()
check("다시 열어도 그대로 (멱등)",
      db.execute("SELECT COUNT(*) FROM usage").fetchone()[0], 2)

base = {"provider": "codex", "account_email": "lab@x", "session_id": "S2",
        "model": "gpt-5.6-sol", "input_tokens": 7, "output_tokens": 3,
        "cache_read_tokens": 40, "cache_creation_tokens": 0}
ai_store.ingest_batch(db, {"host": "n", "usage": [dict(base, uuid="codex:S2:111:1", ts=111)]})
ai_store.ingest_batch(db, {"host": "n", "usage": [dict(base, uuid="codex:S2:999:4", ts=999)]})
check("구버전 송신기가 다른 키로 다시 보내도 한 번만 센다",
      db.execute("SELECT COUNT(*) FROM usage WHERE session_id='S2'").fetchone()[0], 1)

ai_store.ingest_batch(db, {"host": "n", "usage": [{
    "uuid": "t1", "ts": 5, "provider": "claude", "account_email": "lab@x",
    "session_id": "T", "model": "claude-opus-5-5", "input_tokens": 1, "output_tokens": 1,
    "cache_creation_tokens": 900, "cache_creation_5m_tokens": 100,
    "cache_creation_1h_tokens": 800, "speed": "fast", "effort": "xhigh"},
    {"uuid": "t2", "ts": 5, "provider": "claude", "account_email": "lab@x",
     "session_id": "T", "model": "claude-opus-5-5", "input_tokens": 1, "output_tokens": 2,
     "cache_creation_tokens": 50}]})
r1 = db.execute("SELECT speed, effort, cache_creation_1h FROM usage WHERE uuid='t1'").fetchone()
r2 = db.execute("SELECT cache_creation_1h FROM usage WHERE uuid='t2'").fetchone()
check("송신기가 보낸 단가 값이 저장된다", tuple(r1), ("fast", "xhigh", 800))
check("TTL 을 안 보낸 쓰기는 NULL(=모름)", r2[0], None)
db.close()

# ---- 사람 판정: 세션 고정이 경로 규칙보다 먼저 ------------------------------
rules = [{"owner": "kdg", "cwd_glob": "*kdg*"}, {"owner": "woo", "host": "b2"}]
pinned = {"S-tmp": "kdg"}
check("경로가 /tmp 여도 고정된 세션은 그 사람",
      ai_bundle.owner_of(rules, {"session_id": "S-tmp", "cwd": "/tmp", "host": "a"}, pinned), "kdg")
check("고정이 서버 규칙보다 먼저",
      ai_bundle.owner_of(rules, {"session_id": "S-tmp", "cwd": "/x", "host": "b2"}, pinned), "kdg")
check("고정 안 된 세션은 경로 규칙대로 (Claude scratchpad 경로)",
      ai_bundle.owner_of(rules, {"session_id": "S2", "cwd": "/tmp/claude-1006/-home-kdg-sglang/u/scratchpad",
                                 "host": "a"}, pinned), "kdg")
check("어디에도 안 걸리면 None(=미분류)",
      ai_bundle.owner_of(rules, {"session_id": "S3", "cwd": "/tmp", "host": "a"}, pinned), None)

print()
if fails:
    print(f"실패 {len(fails)}건: {', '.join(fails)}")
    sys.exit(1)
print("전부 통과")
