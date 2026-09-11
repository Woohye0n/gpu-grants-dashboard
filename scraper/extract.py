"""Pull GPU models, quantities, usage periods and cost out of announcement text.

Announcements are prose + HWP tables, so nothing here is a strict parser: every
field keeps the sentence it came from (`evidence`) so a human can check it, and
`overrides.json` can correct anything the heuristics get wrong.
"""
from __future__ import annotations

import difflib
import re

from .common import parse_dt, parse_range

# Longest-first: 'L40S' must win over 'L40', 'A6000 Ada' over 'A6000'.
GPU_MODELS: list[tuple[str, str]] = [
    (r"GB200",                      "GB200"),
    (r"B200",                       "B200"),
    (r"B100",                       "B100"),
    (r"H200",                       "H200"),
    (r"H100",                       "H100"),
    (r"H800",                       "H800"),
    (r"A100",                       "A100"),
    (r"A800",                       "A800"),
    (r"V100",                       "V100"),
    (r"L40S",                       "L40S"),
    (r"L40(?![0-9S])",              "L40"),
    (r"(?<![A-Za-z0-9])L4(?![0-9])", "L4"),
    (r"(?<![A-Za-z0-9])T4(?![0-9])", "T4"),
    (r"RTX\s*6000\s*Ada",           "RTX 6000 Ada"),
    (r"RTX\s*(?:4090|5090|3090)",   None),      # keep the matched text
    (r"A6000\s*Ada",                "A6000 Ada"),
    (r"A6000",                      "A6000"),
    (r"A5000",                      "A5000"),
    (r"A4000",                      "A4000"),
    (r"A40(?![0-9])",               "A40"),
    (r"A30(?![0-9])",               "A30"),
    (r"A10(?![0-9])",               "A10"),
    (r"MI300X?",                    "MI300X"),
    (r"MI250X?",                    "MI250"),
    (r"Gaudi\s*[23]|가우디\s*[23]",   None),
    (r"사피온\s*X?\d*",              "사피온"),
    (r"리벨리온|(?<![A-Za-z])ATOM\+?(?![A-Za-z])", "리벨리온 ATOM"),
    (r"딥엑스|DEEPX",                "DEEPX"),
]
_MODEL_RE = re.compile("|".join(f"(?:{p})" for p, _ in GPU_MODELS), re.I)

# Quantity shapes seen in these announcements: 8장 / 서버 2대 / 4개 / 256장 내외.
# The lookbehind keeps the digits of a model name ('H200 서버') out of the match.
_QTY_RE = re.compile(r"(?<![A-Za-z0-9])\d[\d,]*\s*(?:장|대|개|매|노드|서버|기|식)")
_BLANK_QTY = re.compile(r"^0+\s*(?:장|대|개|매|노드|서버|기|식)$")   # 신청서 양식의 빈칸 ("00장")
_VRAM_RE = re.compile(r"(\d{2,3})\s*GB", re.I)

_PERIOD_KEYS = re.compile(
    r"(지원\s*기간|이용\s*기간|사용\s*기간|사업\s*기간|자원\s*이용|서비스\s*기간|이용\s*가능\s*기간|운영\s*기간)")
_SCALE_KEYS = re.compile(r"(지원\s*규모|사업\s*예산\s*규모|지원\s*내용|자원\s*규모|총\s*규모|공급\s*규모|선정\s*규모)")
_COST_KEYS = re.compile(r"(자부담|부담금|이용료|임차료|크레딧|무상|유상|만원|원\s*/\s*월|할인)")

_DATE_ISH = re.compile(r"(?:\d{4}|[’'‘]?\d{2})\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}")


def _canon(match: str) -> str:
    """Map a raw match back to its canonical model label."""
    for pattern, label in GPU_MODELS:
        if re.fullmatch(pattern, match, re.I):
            return label or re.sub(r"\s+", " ", match).upper()
    return re.sub(r"\s+", " ", match).upper()


def find_models(text: str) -> list[str]:
    """Canonical GPU model names, in order of first appearance."""
    seen: list[str] = []
    for m in _MODEL_RE.finditer(text or ""):
        label = _canon(m.group(0))
        if label not in seen:
            seen.append(label)
    return seen


def _spec_score(line: str, paired: bool) -> int:
    """Rank candidate lines so the card shows the real allocation table, not asides."""
    score = 3 if paired else 0
    if re.search(r"[A-Za-z0-9]\s*[(（]\s*\d+\s*장", line):
        score += 3
    if re.search(r"지원\s*(내용|범위|규모|유형)|제공|배정|최소|최대|이용\s*가능", line):
        score += 2
    if re.search(r"만원|원\s*/\s*월|할인|자부담|부담금|납부|정산", line):
        score -= 5
    if re.search(r"[(（]\s*예\s*[)）]|예시|경우|신청부터|참고", line):
        score -= 3
    if line.lstrip().startswith(("※", "*", "-")):
        score -= 2
    return score


def find_specs(text: str, limit: int = 8, per_model: int = 2) -> list[dict]:
    """`{model, qty, vram, line}` rows: a GPU model paired with its quantity.

    HWP tables arrive one cell per line, so a bare model line ('H200') is paired
    with the quantity cell that follows it; candidates are then ranked so the
    allocation table outranks passing mentions in footnotes and fee tables.
    """
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in (text or "").split("\n")]
    lines = [ln for ln in lines if ln]
    cands: list[tuple[int, dict]] = []
    seen: set[tuple[str, str]] = set()

    for i, line in enumerate(lines):
        if len(line) > 400 or _is_toc(line):
            continue
        models = find_models(line)
        if not models:
            continue
        paired, detail = False, (line if _QTY_RE.search(line) else "")
        if not detail:                                   # look ahead for the cell after it
            for nxt in lines[i + 1:i + 3]:
                if _QTY_RE.search(nxt) and not find_models(nxt) and len(nxt) < 200:
                    paired, detail = True, f"{line} — {nxt}"
                    break
        if not detail:
            continue
        score = _spec_score(detail, paired)
        vram = _VRAM_RE.search(detail)
        whole = " / ".join(dict.fromkeys(
            q.strip() for q in _QTY_RE.findall(detail) if not _BLANK_QTY.match(q.strip())))
        if not whole:
            continue
        for model in models:
            qty = whole if paired else (_qty_near(detail, model) or whole)
            phrase = _qty_phrase(detail, model, paired) or qty
            key = (model, phrase)
            if key in seen:
                continue
            seen.add(key)
            cands.append((score, {
                "model": model,
                "qty": phrase,
                "qty_tokens": qty,
                "vram": f"{vram.group(1)}GB" if vram else "",
                "line": detail[:220],
            }))

    cands.sort(key=lambda t: -t[0])
    out: list[dict] = []
    per: dict[str, int] = {}
    for score, row in cands:
        if score < 0 and out:
            break
        if per.get(row["model"], 0) >= per_model:
            continue
        per[row["model"]] = per.get(row["model"], 0) + 1
        out.append(row)
        if len(out) >= limit:
            break
    return out


_TIDY_L = re.compile(r"^[\s\-–—ㆍ·:：,、/_.…‥]+")
_TIDY_R = re.compile(r"[\s,、·/]+$")


def _tidy(seg: str) -> str:
    seg = re.sub(r"\s+", " ", seg)
    seg = _TIDY_R.sub("", _TIDY_L.sub("", seg))
    seg = re.sub(r"^\d{2,3}\s*GB\s*", "", seg).strip()       # VRAM is shown separately
    return _balance(seg)


def _balance(seg: str) -> str:
    """Cutting mid-phrase leaves half a bracket; close it, then unwrap if the
    whole phrase turns out to be one parenthesis ('(2장)' -> '2장')."""
    missing = seg.count("(") - seg.count(")")
    if missing > 0:
        seg += ")" * missing
    while seg.startswith(")"):
        seg = seg[1:].lstrip()
    while seg.startswith("(") and seg.endswith(")") and _wraps(seg):
        seg = seg[1:-1].strip()
    return seg


def _wraps(seg: str) -> bool:
    """True when seg's first '(' is closed only by its last ')'."""
    depth = 0
    for i, ch in enumerate(seg):
        depth += (ch == "(") - (ch == ")")
        if depth == 0:
            return i == len(seg) - 1
    return False


def _group_end(line: str, end: int) -> int:
    """Skip alternatives written together ('H100/H200') so the quantity that
    follows the group is credited to every model in it."""
    while True:
        m = re.match(r"\s*[/·,]\s*", line[end:])
        if not m:
            return end
        nxt = _MODEL_RE.match(line, end + m.end())
        if not nxt:
            return end
        end = nxt.end()


def _qty_phrase(line: str, model: str, paired: bool) -> str:
    """The announcement's own wording for this model's quantity.

    '신청자 당 A100(2장), H100/H200(1장, 2장, 4장)' -> A100: '2장', H100: '1장, 2장, 4장'
    """
    if paired:
        _, sep, tail = line.partition("—")
        return _tidy(tail if sep else line)[:120]
    best = ""
    for m in _MODEL_RE.finditer(line):
        if _canon(m.group(0)) != model:
            continue
        start = _group_end(line, m.end())
        seg = line[start:start + 60]
        nxt = _MODEL_RE.search(seg)
        if nxt:                                          # stop before the next model's numbers
            seg = seg[:nxt.start()]
        if seg.lstrip().startswith("("):                 # '(1장, 2장, 4장)' — keep the whole group
            close = seg.find(")")
            if close > 0:
                seg = seg[:close + 1]
        qs = list(_QTY_RE.finditer(seg))
        if not qs:
            continue
        cut = qs[-1].end()
        if seg[cut:cut + 1] == ")" and seg.count("(") > seg.count(")"):
            cut += 1                                     # close a parenthesis we opened
        cand = _tidy(seg[:cut])
        if len(cand) > len(best):
            best = cand
    return best[:120]


def _qty_near(line: str, model: str) -> str:
    """Quantities sitting next to this model — 'A100(2장), H100/H200(1장)' splits cleanly."""
    found: list[str] = []
    for m in _MODEL_RE.finditer(line):
        if _canon(m.group(0)) != model:
            continue
        lo, hi = max(0, m.start() - 12), min(len(line), m.end() + 26)
        while lo < m.start() and line[lo] in "0123456789,":   # never start mid-number
            lo += 1
        while hi > m.end() and line[hi - 1] in "0123456789,":
            hi -= 1
        found.extend(q.strip() for q in _QTY_RE.findall(line[lo:hi])
                     if not _BLANK_QTY.match(q.strip()))
    return " / ".join(dict.fromkeys(found))


def _is_toc(line: str) -> bool:
    """Table-of-contents rows ('5. 자부담금 납부 7') carry no information."""
    return bool(re.match(r"^\s*\d+\.\s*\S.{0,40}\s+\d{1,3}\s*$", line))


_LEAD_MARK = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.)]\s*|[□■○●◯ㅇ◦▪▸·*※]+\s*)+")


def strip_lead(line: str) -> str:
    """Drop the HWP bullet glyphs ('□', 'ㅇ', '※') that start most 공고 lines."""
    return _LEAD_MARK.sub("", line or "").strip()


def _keyed_lines(text: str, keys: re.Pattern, want: re.Pattern | None = None,
                 limit: int = 4) -> list[str]:
    out: list[str] = []
    for raw in (text or "").split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or len(line) > 300 or not keys.search(line) or _is_toc(line):
            continue
        if want and not want.search(line):
            continue
        if line not in out:
            out.append(line)
        if len(out) >= limit:
            break
    return out


def find_usage_period(text: str) -> dict:
    """The period the granted GPUs may actually be used for."""
    for line in _keyed_lines(text, _PERIOD_KEYS, limit=8):
        if "신청" in line and "기간" in line and not _PERIOD_KEYS.search(line.split("신청")[0]):
            continue                                     # that is the application window
        dates = _DATE_ISH.findall(line)
        if len(dates) >= 2:
            start, end = parse_range(line[line.find(dates[0]):])
            if start:
                return {"text": strip_lead(line), "start": start, "end": end}
        if dates:
            tail = line[line.find(dates[0]):]
            end = parse_dt(tail)
            if end and re.search(r"까지|~|∼", line):
                return {"text": strip_lead(line), "start": None, "end": end}
    # no explicit dates: keep the sentence ('5개월 내외', '협약체결일로부터')
    for line in _keyed_lines(text, _PERIOD_KEYS, re.compile(r"개월|년|까지|~|∼"), limit=1):
        return {"text": strip_lead(line), "start": None, "end": None}
    return {"text": "", "start": None, "end": None}


_REAL_QTY = re.compile(r"\d[\d,]*\s*(?:장|대|개사|개|식|노드|서버|억|백만원|천만원|만원|원|%)")


def find_scale(text: str) -> list[str]:
    """How much is on offer in total — skips bare section headings ('2. 지원내용')."""
    lines = _keyed_lines(text, _SCALE_KEYS, _REAL_QTY, limit=20)
    return [ln for ln in lines if not re.match(r"^\s*\d+\s*[.)]\s*\S{0,12}$", ln)][:3]


def _cost_score(line: str) -> int:
    score = 0
    if re.search(r"만원|원\s*/\s*월|원/월|억원", line):
        score += 4
    if re.search(r"자부담|크레딧|무상|할인", line):
        score += 2
    if re.search(r"납부|기한|취소|정산|반환|제출|증빙|회계|재무|안내\s*예정|방법", line):
        score -= 4
    if re.search(r"운영체제|\bOS\b|소프트웨어|라이선스|스토리지|네트워크|교육|컨설팅|보험|임대료", line):
        score -= 4
    if len(line) > 120:                                  # long prose is not a rate table
        score -= 2
    return score


_BARE_AMOUNT = re.compile(r"^[\d,]+\s*(?:만원|억원|원)(?:\s*/\s*월|/월|\s*\(월\))?$")

# 공고는 지원 대상별로 부담금을 따로 적는다. 라벨이 줄 맨 앞에 괄호로 붙은 경우만
# 신뢰한다 — 본문 아무 곳의 '기업' 두 글자를 대상 표기로 오인하지 않기 위해서다.
_LABEL_HEAD = re.compile(r"^\s*[*※\-–—ㆍ·○ㅇ□]*\s*[(（\[]([^)）\]]{2,14})[)）\]]")
_AUDIENCES: list[tuple[str, str]] = [
    ("학계·연구계", r"학계\s*[·ㆍ,/]\s*연구계|산\s*[·ㆍ]\s*학\s*[·ㆍ]\s*연|학\s*[·ㆍ]\s*연"),
    ("학계",       r"학계|대학"),
    ("연구계",     r"연구계|연구소|출연연"),
    ("산업계",     r"산업계|기업|스타트업|중소|벤처"),
]
# 학계를 먼저 보여준다 (이 대시보드를 보는 쪽이 대학·연구기관이다)
_AUD_ORDER = {"학계": 0, "학계·연구계": 1, "연구계": 2, "산업계": 3, "": 4}


_EXEMPT = re.compile(r"자부담|부담금|이용료|임차료")
_EXEMPT_VERB = re.compile(r"제외|면제|미부과|부과하지|부과되지|무상")
_ACADEMIC = re.compile(r"대학|학계|산학협력단")
_RESEARCH = re.compile(r"연구계|출연연|연구소")


def _audience_of(line: str) -> str:
    """줄 맨 앞 괄호 라벨이 지원 대상이면 그 이름을 준다.

    라벨이 없어도 '대학교(원)…는 자부담금 부여 대상에서 제외' 처럼 특정 대상의
    부담금 규칙을 적은 문장은 그 대상으로 본다.
    """
    m = _LABEL_HEAD.match(line)
    if m:
        head = m.group(1)
        for label, pattern in _AUDIENCES:
            if re.search(pattern, head):
                return label
    if _EXEMPT.search(line) and _EXEMPT_VERB.search(line):
        academic, research = _ACADEMIC.search(line), _RESEARCH.search(line)
        if academic and research:
            return "학계·연구계"
        if academic:
            return "학계"
        if research:
            return "연구계"
    return ""


def find_cost(text: str) -> list[dict]:
    """지원 대상별 부담금. `{audience, text}` 를 학계 우선으로 돌려준다."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in (text or "").split("\n")]
    lines = [ln for ln in lines if ln]
    cands: list[tuple[int, int, str, str]] = []
    for i, line in enumerate(lines):
        if len(line) > 300 or _is_toc(line) or not _COST_KEYS.search(line):
            continue
        if not re.search(r"\d|무상|면제", line):
            continue
        shown = line
        if _BARE_AMOUNT.match(line):                     # 표의 금액 칸 하나 — 라벨을 빌려온다
            for prev in reversed(lines[max(0, i - 3):i]):
                if not _BARE_AMOUNT.match(prev) and len(prev) < 120 and re.search(r"[가-힣A-Za-z]", prev):
                    shown = f"{prev} — {line}"
                    break
        aud = _audience_of(line)
        cands.append((-_cost_score(line), i, aud, _strip_label(shown, aud)))

    # 대상별로 가장 잘 적힌 줄을 고르고, 학계 → 연구계 → 산업계 → 무표기 순으로 낸다
    best: dict[str, list[tuple[int, int, str]]] = {}
    for neg, i, aud, shown in sorted(cands):
        if -neg <= 0:
            continue
        rows = best.setdefault(aud, [])
        cap = 3 if not aud else 2
        if len(rows) >= cap or any(_near(shown, r[2]) for r in rows):
            continue
        rows.append((neg, i, shown))

    out: list[dict] = []
    labelled = any(a for a in best)
    for aud in sorted(best, key=lambda a: (_AUD_ORDER.get(a, 9), a)):
        if labelled and not aud:
            continue          # 대상별로 적힌 줄이 있으면 표의 금액 조각은 군더더기다
        for _, _, shown in best[aud]:
            out.append({"audience": aud, "text": shown})
    return out[:5]


def _near(a: str, b: str) -> bool:
    """'…연구결과를 공개하여야 함' / '…학계·연구계의 경우 연구결과를…' 같은 사실상 같은 문장.

    금액이 다르면 같은 문장이 아니다 — 일반 기업 40만원/월과 청년 기업 20만원/월은
    표의 다른 칸이고, 문장 모양만 거의 같다.
    """
    if re.findall(r"\d[\d,]*", a) != re.findall(r"\d[\d,]*", b):
        return False
    ka, kb = re.sub(r"\W", "", a), re.sub(r"\W", "", b)
    if ka in kb or kb in ka:
        return True
    return difflib.SequenceMatcher(None, ka, kb).ratio() >= 0.8


def _strip_label(line: str, audience: str) -> str:
    """라벨은 따로 보여주므로 문장 앞의 '(산업계)' 는 덜어낸다."""
    if not audience:
        return _LEAD_MARK.sub("", line).strip()
    return _LEAD_MARK.sub("", _LABEL_HEAD.sub("", line, count=1)).strip()


# 가속기 이름처럼 생겼는데 GPU_MODELS 에 없는 토큰. 새 기종(B300, GB300, MI355X …)이
# 나오면 조용히 놓치는 대신 "이 줄을 목록에 추가하라"고 알리기 위한 장치다.
_CANDIDATE = re.compile(r"(?<![A-Za-z0-9])(?:GB|RTX|MI|[A-Z])\d{2,4}[A-Za-z]{0,3}(?![A-Za-z0-9])")
_GPU_CONTEXT = re.compile(r"GPU|가속기|그래픽|엔비디아|NVIDIA|AMD|인텔|장\)|\d\s*장|서버|노드", re.I)
# 공고문에 흔한, 가속기가 아닌 것들
_NOT_A_CHIP = re.compile(r"^(?:KS|ISO|IEC|TTA|VAT|NO|PC|IP|OS|SW|HW|AI|ML|DB|RM)\d", re.I)


def find_unknown_models(text: str, limit: int = 6) -> list[dict]:
    """알려진 목록에 없는 가속기 후보. `{token, line}` 목록."""
    out: list[dict] = []
    seen: set[str] = set()
    known = {m.upper() for m in find_models(text)}
    for raw in (text or "").split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or len(line) > 300 or not _GPU_CONTEXT.search(line):
            continue
        for m in _CANDIDATE.finditer(line):
            token = m.group(0).upper()
            if token in seen or token in known or _NOT_A_CHIP.match(token):
                continue
            if _MODEL_RE.fullmatch(m.group(0)):          # 이미 아는 기종
                continue
            if re.search(r"(?:제|공고|호)\s*$", line[:m.start()][-3:]):
                continue                                 # 공고번호 (제2026-0395호)
            seen.add(token)
            out.append({"token": token, "line": line[:160]})
            if len(out) >= limit:
                return out
    return out


# --------------------------------------------------------------------------- #
# Relevance
# --------------------------------------------------------------------------- #
STRONG = re.compile(
    r"GPU|고성능\s*컴퓨팅|AI\s*컴퓨팅|컴퓨팅\s*(자원|인프라)|연산\s*자원|그래픽\s*처리\s*장치"
    r"|H100|H200|A100|B200|GB200|L40S|슈퍼컴퓨|NPU\s*(자원|지원|인프라)", re.I)
NEGATIVE = re.compile(r"입찰|용역|채용|낙찰|계약\s*체결|결과\s*(공고|발표)|선정\s*결과"
                      r"|성과\s*공유|공유회|보고회|설명회|간담회|세미나|워크숍|웨비나|강연")
SUPPLIER = re.compile(r"공급사|공급\s*기업|운영기관|수행기관|위탁기관|구축\s*·?\s*운용|확보\s*·?\s*구축")


def is_gpu_program(*fields: str) -> bool:
    blob = " ".join(f for f in fields if f)
    return bool(STRONG.search(blob)) and not NEGATIVE.search(blob)


def audience(*fields: str) -> str:
    """Who the announcement is recruiting."""
    blob = " ".join(f for f in fields if f)
    if SUPPLIER.search(blob):
        return "supplier"
    return "user"


_APPLY_KEYS = re.compile(r"(모집\s*기간|신청\s*기간|접수\s*기간|신청\s*·?\s*접수|공모\s*기간|접수\s*일정)")


def find_apply_period(text: str, fallback_year: str | None = None) -> tuple[str | None, str | None]:
    """Application window stated in the body text ('(모집기간) 2026. 9. 2. ~ 9. 16.')."""
    for line in _keyed_lines(text, _APPLY_KEYS, _DATE_ISH, limit=6):
        start, end = parse_range(line)
        if start or end:
            return start, end
    if fallback_year:
        m = re.search(r"[~∼]\s*(\d{1,2})\s*[./월]\s*(\d{1,2})", text or "")
        if m:
            return None, parse_dt(f"{fallback_year}.{m.group(1)}.{m.group(2)}")
    return None, None
