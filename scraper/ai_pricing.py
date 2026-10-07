"""토큰을 크레딧으로 바꾼다 — 공식 API 단가표 기준.

왜 토큰 합계가 아니라 크레딧인가
--------------------------------
토큰 1개의 값은 종류에 따라 최대 수백 배 차이가 납니다. 같은 Claude Opus 5.5 에서도

    입력 $4 · 출력 $20 · 캐시 읽기 $0.20 · 5분 캐시 쓰기 $5 · 1시간 캐시 쓰기 $8   (/1M 토큰)

이고, 모델이 바뀌면(gpt-5.6-luna 입력 $0.20 ~ Claude Fable 5 출력 $50) 더 벌어집니다.
그런데 화면의 "토큰 합계" 는 이것을 전부 1:1 로 더했습니다. 캐시 읽기가 합계의 95%
이상을 차지하는 에이전트 세션에서는 합계가 사실상 '캐시를 얼마나 다시 읽었나' 를
보여 줄 뿐, 누가 비싸게 썼는지는 말해 주지 못합니다.

크레딧은 각 토큰을 그 종류의 단가로 환산해 더한 값입니다.

    1 크레딧 = $10 = 단가 $10/1M 인 토큰 1M 개
    크레딧   = Σ 토큰 수 × 단가($/1M) ÷ 1,000,000 ÷ 10

연구실 계정은 구독제라 실제로 이 금액이 청구되지는 않습니다. 같은 일을 API 로
했다면 얼마였을지로 환산해, 서로 다른 모델·도구·캐시 사용을 한 축에서 비교하려는
것입니다.

단가를 가르는 것 (전부 공식 문서 기준, PRICING_AS_OF 시점)
-------------------------------------------------------------
* 모델                        — 아래 표.
* 토큰 종류                   — 입력 / 출력 / 캐시 읽기 / 캐시 쓰기.
* Claude 캐시 쓰기 TTL         — 5분 쓰기는 입력가 ×1.25, 1시간 쓰기는 ×2.
* 처리 속도 (speed)            — Claude fast mode: Opus 5.5·5·4.8 에서 ×2 (캐시 배수도 그
                                위에 그대로 얹힌다). Opus 4.6 은 fast 를 요청해도 표준
                                속도·표준 단가로 처리된다.
                                OpenAI Fast(옛 이름 priority): ×2, Flex: ×0.5.
* OpenAI 장문맥                 — 한 요청의 입력이 272K 를 넘으면 입력·캐시 ×2, 출력 ×1.5.
                                (Claude 4.6 이후 모델은 1M 까지 표준가 — 할증 없음.)

단가를 바꾸지 않는 것
* thinking / reasoning 토큰    — 출력 토큰으로, 출력 단가로 과금된다 (양쪽 공식 문서).
* effort                      — 생성량을 바꿀 뿐 토큰당 단가는 같다. 화면에서는 분류
                                용도로만 쓴다.

모르는 것은 추정하지 않는다
---------------------------
표에 없는 모델, 그 모델에 없는 속도 티어는 0 크레딧으로 조용히 넘기지 않고
``unpriced`` 로 따로 모아 화면에 "단가 미등록" 으로 드러냅니다. 0 으로 넘기면 비싼
신모델을 쓴 사람이 오히려 적게 쓴 것처럼 보입니다.

단가가 바뀌면 이 파일의 표를 고치거나, ``ai_snapshot.json`` 의 ``pricing.models`` 로
모델 단위 덮어쓰기를 하면 됩니다(예: 프로모션가 종료).
"""
from __future__ import annotations

import re

USD_PER_CREDIT = 10.0
PRICING_AS_OF = "2026-10-07"
PRICING_SOURCES = [
    "https://platform.claude.com/docs/en/about-claude/pricing",
    "https://developers.openai.com/api/docs/pricing",
]

# OpenAI 장문맥 기준: "Short context: ≤272K input tokens. Long context: >272K input tokens."
OPENAI_LONG_CONTEXT_OVER = 272_000

# 토큰 종류 → 단가표의 칸
#   Claude 는 캐시 쓰기를 TTL 로 나누고, OpenAI 는 TTL 구분이 없다.
COMPONENTS = ("input", "output", "cache_read",
              "cache_write", "cache_write_5m", "cache_write_1h", "cache_write_ttl_unknown")
_RATE_KEY = {
    "input": "in", "output": "out", "cache_read": "cr",
    "cache_write": "cw",            # OpenAI: TTL 없음 (입력가 ×1.25)
    "cache_write_5m": "cw5m",       # Claude: 입력가 ×1.25
    "cache_write_1h": "cw1h",       # Claude: 입력가 ×2
    # 송신기를 올리기 전 기록은 TTL 을 모른다. 실측상 Claude Code 의 캐시 쓰기는
    # 97~100% 가 1시간짜리라 1시간 단가로 매긴다(화면에도 따로 표시한다).
    # 이 행들은 retain_days 가 지나면 사라지고, 주간 창에서는 7일 뒤 없어진다.
    "cache_write_ttl_unknown": "cw1h",
}

_CLAUDE_FAST = {"standard": 1.0, "fast": 2.0}
_CLAUDE_STD = {"standard": 1.0}

# $/1M 토큰. Anthropic 표는 5분/1시간 쓰기와 캐시 읽기를 모델별로 직접 적는다
# (캐시 읽기 배수가 모델마다 다르다: 기본 ×0.1, Opus 5.5 ×0.05, Fable 5.1 ×0.025).
DEFAULT_MODELS = {
    # ---- Anthropic ------------------------------------------------------------
    "claude-fable-5-1":  {"family": "claude", "in": 10, "out": 50, "cr": 0.25, "cw5m": 12.5, "cw1h": 20, "speed": _CLAUDE_STD},
    "claude-mythos-5-1": {"family": "claude", "in": 10, "out": 50, "cr": 0.25, "cw5m": 12.5, "cw1h": 20, "speed": _CLAUDE_STD},
    "claude-fable-5":    {"family": "claude", "in": 10, "out": 50, "cr": 1.00, "cw5m": 12.5, "cw1h": 20, "speed": _CLAUDE_STD},
    "claude-mythos-5":   {"family": "claude", "in": 10, "out": 50, "cr": 1.00, "cw5m": 12.5, "cw1h": 20, "speed": _CLAUDE_STD},
    "claude-opus-5-5":   {"family": "claude", "in": 4,  "out": 20, "cr": 0.20, "cw5m": 5,    "cw1h": 8,  "speed": _CLAUDE_FAST},
    "claude-opus-5":     {"family": "claude", "in": 5,  "out": 25, "cr": 0.50, "cw5m": 6.25, "cw1h": 10, "speed": _CLAUDE_FAST},
    "claude-opus-4-8":   {"family": "claude", "in": 5,  "out": 25, "cr": 0.50, "cw5m": 6.25, "cw1h": 10, "speed": _CLAUDE_FAST},
    "claude-opus-4-7":   {"family": "claude", "in": 5,  "out": 25, "cr": 0.50, "cw5m": 6.25, "cw1h": 10, "speed": _CLAUDE_STD},
    # Opus 4.6 은 speed=fast 를 받아도 표준 속도·표준 단가로 처리된다.
    "claude-opus-4-6":   {"family": "claude", "in": 5,  "out": 25, "cr": 0.50, "cw5m": 6.25, "cw1h": 10, "speed": {"standard": 1.0, "fast": 1.0}},
    "claude-opus-4-5":   {"family": "claude", "in": 5,  "out": 25, "cr": 0.50, "cw5m": 6.25, "cw1h": 10, "speed": _CLAUDE_STD},
    "claude-sonnet-5-5": {"family": "claude", "in": 2,  "out": 10, "cr": 0.20, "cw5m": 2.5,  "cw1h": 4,  "speed": _CLAUDE_STD},
    "claude-sonnet-5":   {"family": "claude", "in": 2,  "out": 10, "cr": 0.20, "cw5m": 2.5,  "cw1h": 4,  "speed": _CLAUDE_STD},
    "claude-sonnet-4-6": {"family": "claude", "in": 3,  "out": 15, "cr": 0.30, "cw5m": 3.75, "cw1h": 6,  "speed": _CLAUDE_STD},
    "claude-sonnet-4-5": {"family": "claude", "in": 3,  "out": 15, "cr": 0.30, "cw5m": 3.75, "cw1h": 6,  "speed": _CLAUDE_STD},
    "claude-haiku-4-5":  {"family": "claude", "in": 1,  "out": 5,  "cr": 0.10, "cw5m": 1.25, "cw1h": 2,  "speed": _CLAUDE_STD},
    # ---- OpenAI ---------------------------------------------------------------
    # Fast 는 표에 적힌 모든 칸이 정확히 ×2 다 (예: astra $20/$2/$25/$100).
    # Flex 는 gpt-5.6-sol 에만 있다($2/$0.2/$10). astra 의 Ultrafast 는 단가를
    # 확인하지 못해 넣지 않았다 — 쓰이면 '단가 미등록' 으로 드러난다.
    # long_context 가 없는 모델은 272K 초과 요청의 단가를 확인하지 못했다는 뜻이다.
    # 그 경우 표준가로 짐작하지 않고 미등록으로 둔다(지금까지 실측 0건).
    "gpt-6-astra":   {"family": "openai", "in": 10,  "out": 50,  "cr": 1.00, "cw": 12.5,
                      "speed": {"standard": 1.0, "fast": 2.0},
                      "long_context": {"in": 2.0, "cr": 2.0, "cw": 2.0, "out": 1.5}},
    "gpt-6.1-sol":   {"family": "openai", "in": 2,   "out": 10,  "cr": 0.10, "cw": 2.5,
                      "speed": {"standard": 1.0, "fast": 2.0}},
    "gpt-6-sol":     {"family": "openai", "in": 2,   "out": 10,  "cr": 0.20, "cw": 2.5,
                      "speed": {"standard": 1.0, "fast": 2.0}},
    # 문서상 프로모션가이며 "at least through November 21, 2026" 유지된다.
    "gpt-5.6-sol":   {"family": "openai", "in": 4,   "out": 20,  "cr": 0.40, "cw": 5,
                      "speed": {"standard": 1.0, "fast": 2.0, "flex": 0.5},
                      "long_context": {"in": 2.0, "cr": 2.0, "cw": 2.0, "out": 1.5}},
    "gpt-5.6-terra": {"family": "openai", "in": 2,   "out": 12,  "cr": 0.20, "cw": 2.5,
                      "speed": {"standard": 1.0, "fast": 2.0},
                      "long_context": {"in": 2.0, "cr": 2.0, "cw": 2.0, "out": 1.5}},
    "gpt-5.6-luna":  {"family": "openai", "in": 0.2, "out": 1.2, "cr": 0.02, "cw": 0.25,
                      "speed": {"standard": 1.0, "fast": 2.0},
                      "long_context": {"in": 2.0, "cr": 2.0, "cw": 2.0, "out": 1.5}},
    # 단기 문맥 전용(<272K). 캐시 쓰기 단가는 표에 없다.
    "gpt-5.5":       {"family": "openai", "in": 5,   "out": 30,  "cr": 0.50,
                      "speed": {"standard": 1.0}},
    # Codex 가 사용자 작업과 별도 세션으로 돌리는 자동 리뷰. 공식 단가표에 없고,
    # 사용량이 거의 과금되지 않는 것으로 알려져 있어 단가 ≈ 0 으로 둔다. 토큰은
    # 많아 보이지만(주간 888M, 93.8% 가 캐시 읽기) 이것을 '단가 미등록' 경고로 띄우면
    # 실제로는 없는 비용이 있는 것처럼 읽힌다. 화면에는 note 로 정체를 밝혀 둔다.
    "codex-auto-review": {"family": "openai", "in": 0, "out": 0, "cr": 0, "cw": 0,
                          "speed": {"standard": 1.0, "fast": 1.0, "flex": 1.0},
                          "long_context": {"in": 1.0, "cr": 1.0, "cw": 1.0, "out": 1.0},
                          "note": "자동 리뷰 — 단가 ≈ 0 (크레딧 제외)"},
    # gpt-reserve 는 공식 단가표에 없다 — 일부러 넣지 않는다.
}

_DATE_SUFFIX = re.compile(r"-\d{8}$")


def load_models(cfg=None):
    """기본 단가표에 ``ai_snapshot.json`` 의 ``pricing.models`` 덮어쓰기를 얹는다."""
    models = {k: dict(v) for k, v in DEFAULT_MODELS.items()}
    for name, over in (((cfg or {}).get("pricing") or {}).get("models") or {}).items():
        base = models.get(name, {})
        base.update(over or {})
        models[name] = base
    return models


def lookup(models, model):
    """모델 이름으로 단가를 찾는다. 날짜가 붙은 스냅샷 ID 도 받아 준다."""
    if not model:
        return None
    if model in models:
        return models[model]
    return models.get(_DATE_SUFFIX.sub("", model))


def rates(models, model, speed="standard", long_context=False):
    """(종류별 $/1M 단가, 미등록 사유) 를 돌려준다. 단가를 모르면 (None, 사유)."""
    m = lookup(models, model)
    if m is None:
        return None, "모델 단가 미등록"
    speed = speed or "standard"
    mult = (m.get("speed") or {"standard": 1.0}).get(speed)
    if mult is None:
        return None, f"'{speed}' 처리 단가 미등록"
    lc = {}
    # Claude 4.6 이후는 1M 까지 표준가라 장문맥을 따지지 않는다.
    if long_context and m.get("family") == "openai":
        lc = m.get("long_context")
        if not lc:
            return None, "장문맥(>272K) 단가 미확인"
    out = {}
    for comp, key in _RATE_KEY.items():
        base = m.get(key)
        if base is None:
            continue
        out[comp] = float(base) * mult * float(lc.get(key, 1.0))
    return out, None


def credits(tokens, usd_per_mtok):
    """토큰 수와 $/1M 단가로 크레딧을 계산한다 (1 크레딧 = $10)."""
    return (tokens or 0) * (usd_per_mtok or 0.0) / 1_000_000 / USD_PER_CREDIT


def split_components(provider, i, o, cr, cw, cw1h, cw_unknown):
    """저장된 합계 칸을 단가가 서로 다른 종류로 나눈다.

    cw        : 캐시 쓰기 합계
    cw1h      : 그중 1시간 쓰기로 확인된 몫 (TTL 을 아는 행에서만 더한 값)
    cw_unknown: TTL 을 모르는 행의 캐시 쓰기 (구버전 송신기)
    """
    if provider == "codex":
        return {"input": i, "output": o, "cache_read": cr, "cache_write": cw}
    return {"input": i, "output": o, "cache_read": cr,
            "cache_write_5m": max(0, cw - cw1h - cw_unknown),
            "cache_write_1h": cw1h,
            "cache_write_ttl_unknown": cw_unknown}
