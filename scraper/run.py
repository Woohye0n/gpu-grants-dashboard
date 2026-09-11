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

from .common import KST, ROOT, dday, now_kst
from .sources import aiinfrahub, nipa, seoulaihub

SOURCES = [aiinfrahub, nipa, seoulaihub]
SOURCE_SITES = {
    "aiinfrahub": "https://aiinfrahub.kr/project",
    "nipa": "https://www.nipa.kr/home/2-2",
    "seoulaihub": "https://www.seoulaihub.kr/board/board_basic/board_list.asp"
                  "?scrID=0000000170&pageNum=4&subNum=1&ssubNum=1&page=1",
}
# announcements that closed longer ago than this drop off the board
KEEP_CLOSED_DAYS = 400
DOCS = os.path.join(ROOT, "docs")
HISTORY = os.path.join(ROOT, "data", "history")


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
            for field in ("apply_start", "apply_end", "posted", "summary"):
                if not base.get(field) and q.get(field):
                    base[field] = q[field]
            if not (base.get("usage_period") or {}).get("text") and (q.get("usage_period") or {}).get("text"):
                base["usage_period"] = q["usage_period"]
            base["closed_hint"] = base.get("closed_hint") or q.get("closed_hint")
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
    programs, report = [], []
    for mod in SOURCES:
        entry = {"key": mod.SOURCE, "label": mod.SOURCE_LABEL, "url": SOURCE_SITES[mod.SOURCE]}
        try:
            rows = mod.fetch(deep=deep)
            programs.extend(rows)
            entry.update(ok=True, count=len(rows), error="")
        except Exception as exc:                         # noqa: BLE001 - one bad site must not sink the run
            entry.update(ok=False, count=0, error=f"{type(exc).__name__}: {exc}")
            print(f"[warn] {mod.SOURCE} failed: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        report.append(entry)
    return programs, report


def build(deep: bool = True) -> dict:
    today = now_kst()
    programs, report = collect(deep)
    programs = merge(programs)
    for p in programs:
        p["scale"] = dedupe_lines(p.get("scale"))
        p["cost"] = dedupe_lines(p.get("cost"))
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
        "counts": {
            "total": len(programs),
            "open": sum(1 for p in programs if p["status"] == "open"),
            "upcoming": sum(1 for p in programs if p["status"] == "upcoming"),
            "closing_soon": sum(1 for p in programs if p.get("closing_soon")),
            "closed": sum(1 for p in programs if p["status"] == "closed"),
        },
        "programs": programs,
    }


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


def main() -> int:
    ap = argparse.ArgumentParser(description="GPU 지원사업 공고 수집")
    ap.add_argument("--shallow", action="store_true", help="첨부파일을 열지 않고 목록만 수집")
    ap.add_argument("--dry-run", action="store_true", help="파일로 쓰지 않고 요약만 출력")
    args = ap.parse_args()

    payload = build(deep=not args.shallow)
    if not args.dry_run:
        publish(payload)

    c = payload["counts"]
    print(f"[{payload['generated_at_kst']} KST] 총 {c['total']}건 "
          f"(모집중 {c['open']}, 마감임박 {c['closing_soon']}, 예정 {c['upcoming']}, 마감 {c['closed']})")
    for s in payload["sources"]:
        print(f"  - {s['label']}: {'OK' if s['ok'] else 'FAIL ' + s['error']} ({s['count']}건)")
    for p in payload["programs"]:
        if p["status"] in ("open", "upcoming"):
            dd = f"D-{p['dday']}" if (p.get("dday") or 0) >= 0 else ""
            print(f"  * [{p['status']:8s} {dd:>5s}] {p['title'][:52]} | {', '.join(p['gpu_models']) or '-'}")
    return 0 if all(s["ok"] for s in payload["sources"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
