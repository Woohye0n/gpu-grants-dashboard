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

if fails:
    print(f"실패 {len(fails)}건: {', '.join(fails)}")
    sys.exit(1)
print("전부 통과")
