"""Collect every source, merge cross-posted announcements, publish docs/data.js.

Run:  python -m scraper.run            (from the project root)
      python -m scraper.run --shallow  (list pages only, no attachment parsing)
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
import traceback
from datetime import datetime, timedelta

from .common import KST, ROOT, dday, now_kst, out_of_time, set_deadline
from .sources import aiinfrahub, generic, nipa, seoulaihub

BUILTIN = {"aiinfrahub": aiinfrahub, "nipa": nipa, "seoulaihub": seoulaihub}
SOURCE_SITES = {
    "aiinfrahub": "https://aiinfrahub.kr/project",
    "nipa": "https://www.nipa.kr/home/2-2",
    "seoulaihub": "https://www.seoulaihub.kr/board/board_basic/board_list.asp"
                  "?scrID=0000000170&pageNum=4&subNum=1&ssubNum=1&page=1",
}
SOURCES_FILE = os.path.join(ROOT, "sources.json")


def load_sources() -> list[dict]:
    """sources.json 의 목록. 파일이 없거나 깨졌으면 손으로 짠 3곳만 쓴다."""
    fallback = [{"key": k, "kind": "builtin", "module": k} for k in BUILTIN]
    try:
        with open(SOURCES_FILE, encoding="utf-8") as fh:
            conf = json.load(fh)
    except (OSError, ValueError):
        return fallback
    rows = [s for s in (conf.get("sources") or []) if s.get("key") and not s.get("disabled")]
    return rows or fallback


def repo_slug() -> str:
    """'사이트 추가' 폼이 이슈를 열 저장소."""
    try:
        with open(SOURCES_FILE, encoding="utf-8") as fh:
            return (json.load(fh).get("repo") or "").strip()
    except (OSError, ValueError):
        return ""


def _describe(cfg: dict) -> tuple[str, str]:
    """화면에 쓸 (이름, 게시판 주소)."""
    if cfg.get("kind") == "builtin":
        mod = BUILTIN.get(cfg.get("module") or cfg["key"])
        return (mod.SOURCE_LABEL if mod else cfg["key"]), SOURCE_SITES.get(cfg["key"], "")
    return cfg.get("label") or cfg["key"], cfg.get("list_url", "")
# announcements that closed longer ago than this drop off the board
KEEP_CLOSED_DAYS = 400
DOCS = os.path.join(ROOT, "docs")
HISTORY = os.path.join(ROOT, "data", "history")
LAST_GOOD = os.path.join(ROOT, "data", "last_good")
# 마지막 성공 수집을 이만큼 넘게 못 갱신하면 더는 들고 있지 않는다
STALE_DROP_DAYS = 30


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #
def decorate(p: dict, today: datetime) -> dict:
    end, start = p.get("apply_end"), p.get("apply_start")
    days = dday(end, today)
    if p.get("closed_hint"):
        status = "closed"
    elif end and days is not None and days < 0:
        status = "closed"
    elif start and today.strftime("%Y-%m-%dT%H:%M") < start:
        status = "upcoming"
    elif end:
        status = "open"
    else:
        status = "unknown"
    p["status"] = status
    p["dday"] = days if status in ("open", "upcoming") else None
    p["closing_soon"] = bool(status == "open" and days is not None and days <= 7)
    return p


# --------------------------------------------------------------------------- #
# cross-source merge
# --------------------------------------------------------------------------- #
_STRIP = re.compile(r"\[[^\]]*\]|[「」『』()（）·,\.\-–—:~]|공고문?|안내서?|공모|모집|신청|참여기업|사용자|\s+")
# mutually exclusive markers: two announcements that disagree inside one group
# are different announcements, however similar their titles look
_QUAL_GROUPS = [
    {"산업계": r"산업계", "산학연": r"산[·\s]*학[·\s]*연"},
    {"수시": r"수시", "베타": r"베타", "추가": r"추가\s*모집"},
    {"공급사": r"공급사|공급기업", "운영기관": r"운영기관|수행기관"},
]


def _core(p: dict) -> str:
    """The programme name: what sits inside 「」, else the board's 사업명."""
    m = re.search(r"[「『]([^」』]+)[」』]", p.get("title") or "")
    raw = m.group(1) if m else (p.get("program") or p.get("title") or "")
    return _STRIP.sub("", raw).lower()


def _quals(p: dict) -> list[str | None]:
    blob = f"{p.get('title', '')} {p.get('program', '')}"
    return [next((tag for tag, pat in group.items() if re.search(pat, blob)), None)
            for group in _QUAL_GROUPS]


def _nth(p: dict) -> str | None:
    m = re.search(r"(\d{2})\s*-\s*(\d+)\s*차", p.get("title") or "")
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _richness(p: dict) -> int:
    return (2 * len(p.get("gpu_specs") or []) + len(p.get("scale") or [])
            + len(p.get("cost") or []) + (1 if (p.get("usage_period") or {}).get("text") else 0)
            + (1 if p.get("summary") else 0))


def _same(a: dict, b: dict) -> bool:
    """Is this the same announcement cross-posted on another portal?"""
    if a["source"] == b["source"]:
        return False
    ca, cb = _core(a), _core(b)
    if not ca or not cb or difflib.SequenceMatcher(None, ca, cb).ratio() < 0.9:
        return False
    for qa, qb in zip(_quals(a), _quals(b)):            # 산업계 vs 산학연 -> different calls
        if qa and qb and qa != qb:
            return False
    na, nb = _nth(a), _nth(b)
    if na and nb and na != nb:                          # 25-1차 vs 26-1차
        return False
    if _gap(a.get("apply_start"), b.get("apply_start")) <= 7:
        return True                                     # opened the same day = same call
    gap = _gap(a.get("apply_end"), b.get("apply_end"))
    return gap <= 45                                    # one portal may close it early


def _gap(a: str | None, b: str | None) -> int:
    """Days between two dates; unknown on either side counts as 'no objection'."""
    if not a or not b:
        return 0
    try:
        return abs((datetime.strptime(a[:10], "%Y-%m-%d") - datetime.strptime(b[:10], "%Y-%m-%d")).days)
    except ValueError:
        return 0


def merge(programs: list[dict]) -> list[dict]:
    """Fold the same announcement cross-posted on two portals into one card."""
    groups: list[list[dict]] = []
    for p in sorted(programs, key=lambda x: -_richness(x)):
        for g in groups:
            if any(q["source"] == p["source"] for q in g):   # one entry per source
                continue
            if all(_same(p, q) for q in g):
                g.append(p)
                break
        else:
            groups.append([p])

    out = []
    for g in groups:
        base = dict(g[0])
        links: list[dict] = []
        for q in g:
            link = {"label": q["source_label"], "url": q["url"], "source": q["source"]}
            if link not in links:
                links.append(link)
        base["links"] = links
        base["also_on"] = [l["label"] for l in links[1:]]
        for q in g[1:]:
            for m in q.get("gpu_models") or []:
                if m not in base["gpu_models"]:
                    base["gpu_models"].append(m)
            for u in q.get("unknown_models") or []:
                if u["token"] not in {x["token"] for x in base.get("unknown_models") or []}:
                    base.setdefault("unknown_models", []).append(u)
            for field in ("apply_start", "apply_end", "posted", "summary"):
                if not base.get(field) and q.get(field):
                    base[field] = q[field]
            if not (base.get("usage_period") or {}).get("text") and (q.get("usage_period") or {}).get("text"):
                base["usage_period"] = q["usage_period"]
            base["closed_hint"] = base.get("closed_hint") or q.get("closed_hint")
            if base.get("stale") and not q.get("stale"):   # a fresh copy wins
                base["stale"], base["stale_since"] = False, ""
            base["attachments"] = (base.get("attachments") or []) + [
                a for a in (q.get("attachments") or []) if a not in (base.get("attachments") or [])]
        out.append(base)
    return out


_LEAD = re.compile(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ\d]+\s*[.)]\s*|[□○●ㅇ◦▪·*※\-–—]+\s*)+")


def dedupe_lines(items: list[str]) -> list[str]:
    """Drop entries that merely repeat another (PDF and HWP of the same notice)."""
    out: list[str] = []
    for raw in items or []:
        line = _LEAD.sub("", raw).strip()
        core = re.sub(r"\W", "", line)
        if not core or any(core in re.sub(r"\W", "", o) or re.sub(r"\W", "", o) in core for o in out):
            continue
        out.append(line)
    return out


# --------------------------------------------------------------------------- #
# overrides
# --------------------------------------------------------------------------- #
def _dedupe_costs(items: list[dict] | None) -> list[dict]:
    """같은 문장이 첨부 PDF·HWP 양쪽에서 올라오는 것을 걸러낸다."""
    out: list[dict] = []
    for row in items or []:
        if isinstance(row, str):                         # 예전 스키마 방어
            row = {"audience": "", "text": row}
        core = re.sub(r"\W", "", row.get("text", ""))
        if not core or any(core in re.sub(r"\W", "", o["text"]) or
                           re.sub(r"\W", "", o["text"]) in core for o in out):
            continue
        out.append(row)
    return out


def apply_overrides(programs: list[dict]) -> list[dict]:
    path = os.path.join(ROOT, "overrides.json")
    if not os.path.exists(path):
        return programs
    with open(path, encoding="utf-8") as fh:
        conf = json.load(fh)
    by_uid = conf.get("programs") or {}
    out = []
    for p in programs:
        patch = None
        for link in p.get("links") or [{"source": p["source"], "url": p["url"]}]:
            patch = by_uid.get(f"{link['source']}-{link['url'].rstrip('/').rsplit('/', 1)[-1]}") or patch
        patch = by_uid.get(p["uid"]) or patch
        if patch:
            if patch.get("hide"):
                continue
            p = {**p, **{k: v for k, v in patch.items() if k != "hide"}}
            p["edited"] = True
        out.append(p)
    return out + list(conf.get("extra") or [])


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def collect(deep: bool = True) -> tuple[list[dict], list[dict]]:
    """Fetch every source. A site that breaks or blocks us keeps its last good
    rows instead of silently disappearing from the board."""
    programs, report = [], []
    for cfg in load_sources():
        key = cfg["key"]
        label, site = _describe(cfg)
        entry = {"key": key, "label": label, "url": site,
                 "kind": cfg.get("kind", "generic"),
                 "ci_blocked": bool(cfg.get("ci_blocked")),
                 "note": cfg.get("note", "")}
        rows, error = [], ""
        try:
            if cfg.get("kind") == "builtin":
                mod = BUILTIN.get(cfg.get("module") or key)
                if mod is None:
                    raise KeyError(f"알 수 없는 builtin 모듈: {cfg.get('module') or key}")
                rows = mod.fetch(deep=deep)
            else:
                rows = generic.fetch_config(cfg, deep=deep)
        except Exception as exc:                         # noqa: BLE001 - one bad site must not sink the run
            error = f"{type(exc).__name__}: {exc}"
            print(f"[warn] {key} failed: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

        cached, cached_at = _load_last_good(key)
        scanned = generic.LAST_SCAN.get(key, 0)
        if rows and not error:
            _save_last_good(key, rows)
            entry.update(ok=True, stale=False, count=len(rows), error="", collected_at="")
        elif not error and scanned:
            # 목록은 멀쩡히 읽었고 GPU 공고가 없을 뿐이다 — 실패가 아니다
            _save_last_good(key, rows)
            entry.update(ok=True, stale=False, count=0, collected_at="",
                         error=f"게시글 {scanned}건을 읽었고, 그중 GPU 관련 공고는 없었습니다")
        elif cached:
            if error:
                reason = error
            elif out_of_time():
                reason = "수집 시간 초과 (사이트 응답이 없거나 매우 느립니다)"
            else:
                reason = "공고를 한 건도 읽지 못했습니다 (게시판 구조가 바뀌었을 수 있습니다)"
            rows = [{**r, "stale": True, "stale_since": cached_at} for r in cached]
            entry.update(ok=False, stale=True, count=len(rows),
                         error=humanize_error(reason), error_detail=reason,
                         collected_at=cached_at)
            print(f"[warn] {key}: {cached_at} 수집분을 그대로 사용합니다", file=sys.stderr)
        else:
            entry.update(ok=False, stale=False, count=0, collected_at="",
                         error=humanize_error(error) or "수집 결과가 없습니다.",
                         error_detail=error)
        programs.extend(rows)
        report.append(entry)
    return programs, report


def _last_good_path(source: str) -> str:
    return os.path.join(LAST_GOOD, f"{source}.json")


def _save_last_good(source: str, rows: list[dict]) -> None:
    os.makedirs(LAST_GOOD, exist_ok=True)
    payload = {"saved_at": now_kst().strftime("%Y-%m-%d %H:%M"), "rows": rows}
    with open(_last_good_path(source), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)


def _load_last_good(source: str) -> tuple[list[dict], str]:
    path = _last_good_path(source)
    if not os.path.exists(path):
        return [], ""
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return [], ""
    saved_at = payload.get("saved_at", "")
    try:
        age = (now_kst().date() - datetime.strptime(saved_at[:10], "%Y-%m-%d").date()).days
    except ValueError:
        return [], ""
    if age > STALE_DROP_DAYS:
        return [], saved_at
    return payload.get("rows") or [], saved_at


def build(deep: bool = True) -> dict:
    today = now_kst()
    programs, report = collect(deep)
    programs = merge(programs)
    for p in programs:
        p["scale"] = dedupe_lines(p.get("scale"))
        p["cost"] = _dedupe_costs(p.get("cost"))
        decorate(p, today)
    programs = apply_overrides(programs)

    cutoff = (today - timedelta(days=KEEP_CLOSED_DAYS)).strftime("%Y-%m-%d")
    programs = [p for p in programs
                if p.get("status") != "closed" or (p.get("apply_end") or p.get("posted") or "9999") >= cutoff]

    rank = {"open": 0, "upcoming": 1, "unknown": 2, "closed": 3}
    programs.sort(key=lambda p: (rank.get(p.get("status"), 9),
                                 p.get("dday") if p.get("dday") is not None else 9999,
                                 -(len(p.get("apply_end") or "")),
                                 p.get("apply_end") or "",
                                 p.get("posted") or ""))

    return {
        "generated_at": today.isoformat(timespec="minutes"),
        "generated_at_kst": today.strftime("%Y-%m-%d %H:%M"),
        "timezone": "Asia/Seoul (KST)",
        "sources": report,
        "repo": repo_slug(),
        "stale_sources": [s["label"] for s in report if s.get("stale")],
        "unknown_models": _unknown_digest(programs),
        "counts": {
            "total": len(programs),
            "open": sum(1 for p in programs if p["status"] == "open"),
            "upcoming": sum(1 for p in programs if p["status"] == "upcoming"),
            "closing_soon": sum(1 for p in programs if p.get("closing_soon")),
            "closed": sum(1 for p in programs if p["status"] == "closed"),
        },
        "programs": programs,
    }


# 화면에 파이썬 예외를 그대로 쏟으면 읽을 수가 없다. 사람이 읽을 한 문장으로 바꾸고
# 원문은 error_detail 로 따로 남겨 도구설명에서 볼 수 있게 한다.
_ERROR_RULES = [
    (re.compile(r"자동등록방지|보안절차|prove that you are human|captcha", re.I),
     "사이트가 자동 수집을 막고 있습니다 (사람 확인 페이지가 대신 내려옵니다)."),
    (re.compile(r"ConnectTimeout|Read timed out|timed out|시간\s*초과", re.I),
     "사이트가 응답하지 않습니다 (연결 시간 초과). 해외 IP 를 막는 곳일 수 있습니다."),
    (re.compile(r"NameResolution|Failed to resolve|Name or service not known", re.I),
     "주소를 찾을 수 없습니다 (DNS 조회 실패). 주소가 바뀌었는지 확인하세요."),
    (re.compile(r"SSLError|certificate", re.I),
     "보안 연결(SSL)에 실패했습니다."),
    (re.compile(r"ConnectionRefused|Connection refused", re.I),
     "서버가 연결을 거부했습니다."),
    (re.compile(r"행\s*없음"),
     "목록 페이지는 열렸지만 게시글 줄을 찾지 못했습니다 (게시판 구조가 바뀌었을 수 있습니다)."),
    (re.compile(r"게시글 줄을 찾지 못"),
     "목록 페이지는 열렸지만 게시글 줄을 찾지 못했습니다 (게시판 구조가 바뀌었을 수 있습니다)."),
]
_HTTP_CODE = re.compile(r"\b([45]\d{2})\s+(?:Client|Server)\s+Error")


def humanize_error(text: str) -> str:
    """기술적인 예외 문자열을 한 문장으로 줄인다. 못 알아보면 앞부분만 자른다."""
    if not text:
        return ""
    for pattern, message in _ERROR_RULES:
        if pattern.search(text):
            return message
    m = _HTTP_CODE.search(text)
    if m:
        return f"서버가 HTTP {m.group(1)} 를 돌려줬습니다."
    one = re.sub(r"\s+", " ", text).strip()
    return one if len(one) <= 120 else one[:117] + "…"


def _unknown_digest(programs: list[dict]) -> list[dict]:
    """가속기 이름 같은데 목록에 없는 토큰 — 사람이 GPU_MODELS 에 한 줄 추가하라는 신호."""
    seen: dict[str, dict] = {}
    for p in programs:
        for u in p.get("unknown_models") or []:
            row = seen.setdefault(u["token"], {"token": u["token"], "line": u["line"], "programs": []})
            if p["title"] not in row["programs"]:
                row["programs"].append(p["title"][:60])
    return sorted(seen.values(), key=lambda r: r["token"])


_ASSET_Q = re.compile(r'((?:href|src)="\./([\w./-]+\.(?:js|css)))\?v=[^"]*"')

# 각 페이지와 그 페이지가 싣는 자산. 내용이 바뀌면 ?v= 도 바뀌어야 한다.
_PAGES = [
    ("index.html", ["app.js", "style.css", "data.js"]),
    (os.path.join("ai", "index.html"),
     [os.path.join("ai", "app.js"), os.path.join("ai", "style.css"),
      os.path.join("ai", "data", "dashboard.json")]),
]


def stamp_assets() -> dict:
    """각 페이지의 ?v= 를 그 페이지 자산의 내용 해시로 바꾼다.

    고정값(?v=1)으로 두면 파일을 고쳐도 브라우저가 캐시된 옛 js/css 를 계속 쓴다.
    실제로 그래서 고친 화면이 반영되지 않았다 — 내용이 바뀌면 주소도 바뀌어야 한다.
    """
    import hashlib

    tags = {}
    for page, assets in _PAGES:
        index = os.path.join(DOCS, page)
        if not os.path.exists(index):
            continue
        h = hashlib.sha1()
        for name in assets:
            path = os.path.join(DOCS, name)
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    h.update(fh.read())
        tag = h.hexdigest()[:10]
        with open(index, encoding="utf-8") as fh:
            html = fh.read()
        new_html = _ASSET_Q.sub(lambda m: f'{m.group(1)}?v={tag}"', html)
        if new_html != html:
            with open(index, "w", encoding="utf-8") as fh:
                fh.write(new_html)
        tags[page] = tag
    return tags


def publish(payload: dict) -> None:
    os.makedirs(DOCS, exist_ok=True)
    os.makedirs(HISTORY, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False, indent=1)
    with open(os.path.join(DOCS, "data.json"), "w", encoding="utf-8") as fh:
        fh.write(blob)
    # data.js so the dashboard also opens straight off the filesystem (file://)
    with open(os.path.join(DOCS, "data.js"), "w", encoding="utf-8") as fh:
        fh.write("window.GPU_GRANTS = " + blob + ";\n")
    stamp = datetime.fromisoformat(payload["generated_at"]).astimezone(KST).strftime("%Y-%m-%d")
    with open(os.path.join(HISTORY, f"{stamp}.json"), "w", encoding="utf-8") as fh:
        fh.write(blob)
    stamp_assets()          # data.js 도 바뀌므로 매 수집마다 새 해시가 된다


def main() -> int:
    ap = argparse.ArgumentParser(description="GPU 지원사업 공고 수집")
    ap.add_argument("--shallow", action="store_true", help="첨부파일을 열지 않고 목록만 수집")
    ap.add_argument("--dry-run", action="store_true", help="파일로 쓰지 않고 요약만 출력")
    ap.add_argument("--deadline-seconds", type=int, default=0,
                    help="전체 수집 시간 상한(초). 넘기면 남은 사이트는 직전 데이터를 씁니다")
    args = ap.parse_args()

    set_deadline(args.deadline_seconds or None)
    payload = build(deep=not args.shallow)
    if not args.dry_run:
        publish(payload)

    c = payload["counts"]
    print(f"[{payload['generated_at_kst']} KST] 총 {c['total']}건 "
          f"(모집중 {c['open']}, 마감임박 {c['closing_soon']}, 예정 {c['upcoming']}, 마감 {c['closed']})")
    for s in payload["sources"]:
        if s["ok"]:
            state = "OK"
        elif s.get("stale"):
            state = f"STALE({s['collected_at']} 수집분 유지) — {s['error'][:80]}"
        else:
            state = f"FAIL — {s['error'][:80]}"
        print(f"  - {s['label']}: {state} ({s['count']}건)")
    for u in payload.get("unknown_models") or []:
        print(f"  ! 미확인 가속기 후보 '{u['token']}' — GPU_MODELS 에 추가가 필요할 수 있습니다")
        print(f"      근거: {u['line'][:110]}")
    for p in payload["programs"]:
        if p["status"] in ("open", "upcoming"):
            dd = f"D-{p['dday']}" if (p.get("dday") or 0) >= 0 else ""
            print(f"  * [{p['status']:8s} {dd:>5s}] {p['title'][:52]} | {', '.join(p['gpu_models']) or '-'}")
    # 종료 코드로 '대비책이 받아낸 저하' 와 '진짜 고장' 을 구분한다. 둘을 같게 두면
    # 매일 빨간불이 켜져서, 정작 고장났을 때 알아챌 수 없다 (2026-09-15 에 그랬다).
    hard = [s for s in payload["sources"] if not s["ok"] and not s.get("stale")
            and s["count"] == 0 and "GPU 관련 공고는 없었" not in (s.get("error") or "")]
    degraded = [s for s in payload["sources"] if s.get("stale")]
    if hard:
        print(f"[hard] 수집 실패: {', '.join(s['label'] for s in hard)}", file=sys.stderr)
        return 2
    if degraded:
        print(f"[degraded] 직전 데이터 사용: {', '.join(s['label'] for s in degraded)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
