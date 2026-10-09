"""'기록' 탭의 데이터 — 지난 사용을 하루 단위로 묶어 기간을 골라 볼 수 있게 한다.

화면의 다른 탭은 지금부터 거꾸로 센 5시간·7일만 본다. 기록 탭은 사람이 고른
기간(예: 9월 한 달, 10/1~10/15)을 본다. 그래서 여기서는 숫자를 **하루 × 사람 ×
도구 × 계정 × 모델 × 속도 × effort** 로만 묶어 내보내고, 기간 합산은 화면이 한다.
기간을 바꿀 때마다 서버에 묻지 않아도 된다.

이 파일은 저장소에 의존하지 않는다 — 입력은 하루 단위로 미리 묶은 레코드다.
공개 대시보드(scraper/ai_store.usage_daily)와 중앙 서버(usage 테이블)가 각자
레코드를 만들어 넘기고, 단가 환산·사람 판정·압축은 둘 다 이 코드로 한다.
같은 기간이면 두 화면의 크레딧이 같은 방법으로 계산된다.

레코드 한 줄(dict):
    day            'YYYY-MM-DD' (한국 시각)
    provider       'claude' | 'codex'
    account_email, host, session_id, cwd
    model, speed('standard'|'fast'|'flex'), effort('' = 모름), lc(0/1: OpenAI 장문맥)
    n              턴 수
    i, o, cw, cr   입력 / 출력 / 캐시 쓰기 / 캐시 읽기 토큰
    cw1h           캐시 쓰기 중 1시간 TTL 로 확인된 몫
    cwu            TTL 을 모르는 캐시 쓰기 (구버전 송신기·중앙 기록)

사람은 **내보낼 때** 정한다. 저장은 세션 단위로 해 두므로, 사람 규칙을 고치면
과거 기록도 다음 발행부터 새 규칙으로 다시 갈린다.
"""
from __future__ import annotations

try:                                    # 공개 대시보드: scraper 패키지 안
    from . import ai_pricing
except ImportError:                     # pragma: no cover - 중앙 서버: backend 패키지 안
    import ai_pricing                   # type: ignore

SCHEMA = 1
TZ = "Asia/Seoul"
UNASSIGNED = "미분류"
# 하루 경계는 한국 시각 자정. SQLite 에서 ts(ms) 를 그 날짜로 바꾸는 식.
DAY_SQL = "date(ts / 1000 + 32400, 'unixepoch')"
COLS = ["day", "owner", "provider", "account", "model", "speed", "effort",
        "turns", "input", "output", "cache_write", "cache_read", "credits", "unpriced"]
_DIMS = ("day", "owner", "provider", "account", "model", "speed", "effort")


def record_credits(r, models):
    """레코드 하나를 (크레딧, 단가 미등록 토큰) 으로 바꾼다.

    단가를 모르는 모델·종류는 0 크레딧으로 뭉개지 않고 미등록 토큰으로 따로 센다
    (ai_pricing 의 원칙과 같다).
    """
    comps = ai_pricing.split_components(
        r["provider"], r.get("i") or 0, r.get("o") or 0, r.get("cr") or 0,
        r.get("cw") or 0, r.get("cw1h") or 0, r.get("cwu") or 0)
    tokens = sum(comps.values())
    if not tokens:
        return 0.0, 0
    rate, _why = ai_pricing.rates(models, r.get("model"), r.get("speed") or "standard",
                                  bool(r.get("lc")))
    if rate is None:
        return 0.0, tokens
    credits, unpriced = 0.0, 0
    for comp, tok in comps.items():
        if not tok:
            continue
        if comp not in rate:
            unpriced += tok
            continue
        credits += ai_pricing.credits(tok, rate[comp])
    return credits, unpriced


def assemble(records, *, models, owner_of, now_ms, allowed=None,
             sources=None, notes=None):
    """레코드를 사람별로 갈라 하루 단위 표로 압축한다.

    owner_of(record) -> 사람 이름 또는 None(미분류).
    allowed 가 있으면 그 계정의 사용만 싣는다(대시보드의 다른 탭과 같은 기준).
    """
    groups = {}
    no_model = {}                       # 모델 이름이 빠진 기록: day -> 토큰
    for r in records:
        acct = r.get("account_email") or ""
        if allowed and acct not in allowed:
            continue
        credits, unpriced = record_credits(r, models)
        turns = r.get("n") or 0
        tok = ((r.get("i") or 0) + (r.get("o") or 0) + (r.get("cw") or 0) + (r.get("cr") or 0))
        if not turns and not tok:
            continue
        key = (r["day"], owner_of(r) or UNASSIGNED, r.get("provider") or "", acct,
               r.get("model") or "", r.get("speed") or "standard", r.get("effort") or "")
        g = groups.get(key)
        if g is None:
            g = groups[key] = [0, 0, 0, 0, 0, 0.0, 0]
        g[0] += turns
        g[1] += r.get("i") or 0
        g[2] += r.get("o") or 0
        g[3] += r.get("cw") or 0
        g[4] += r.get("cr") or 0
        g[5] += credits
        g[6] += unpriced
        if not r.get("model") and unpriced:
            no_model[r["day"]] = no_model.get(r["day"], 0) + unpriced

    # 문자열은 사전으로 한 번만 싣는다 — 행마다 이름을 반복하면 몇 배가 된다.
    dims = {d: sorted({k[i] for k in groups}) for i, d in enumerate(_DIMS)}
    index = {d: {v: j for j, v in enumerate(vals)} for d, vals in dims.items()}
    rows = []
    for key in sorted(groups):
        g = groups[key]
        rows.append([index[d][key[i]] for i, d in enumerate(_DIMS)]
                    + g[:5] + [round(g[5], 4), g[6]])
    model_notes = {}
    for m in dims["model"]:
        note = (ai_pricing.lookup(models, m) or {}).get("note")
        if note:
            model_notes[m] = note
    days = dims["day"]
    notes = list(notes or [])
    if no_model:
        notes.append(
            f"{min(no_model)}~{max(no_model)} 의 일부 기록({sum(no_model.values()) / 1e9:.2f}B 토큰)은 "
            "송신기가 모델 이름을 보내지 않아 '(모델 미상)' 으로 남았고, 단가를 알 수 없어 크레딧에서 "
            "제외했습니다. 한 세션 안에서도 모델이 바뀌므로 추정해 채우지 않습니다.")
    return {
        "schema": SCHEMA,
        "generated_at": now_ms,
        "tz": TZ,
        "usd_per_credit": ai_pricing.USD_PER_CREDIT,
        "pricing_as_of": ai_pricing.PRICING_AS_OF,
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "cols": COLS,
        "dims": dims,
        "rows": rows,
        "model_notes": model_notes,
        "sources": sources or [],
        "notes": notes,
    }
