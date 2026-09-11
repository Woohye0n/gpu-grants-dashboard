"""정보통신산업진흥원 (nipa.kr) — 알림마당 > 사업공고 (/home/2-2).

Server-rendered board. Each row already carries 사업명 and 신청기간; the GPU
allocation tables live in the .hwp / .hwpx attachments of the detail page.
"""
from __future__ import annotations

import html as _html
import re

from .. import extract as E
from ..common import download, doc_text, get_text, html_to_text, parse_dt

SOURCE = "nipa"
SOURCE_LABEL = "정보통신산업진흥원(NIPA)"
ORG = "과학기술정보통신부·NIPA"
BASE = "https://www.nipa.kr"
LIST = BASE + "/home/2-2?curPage={page}"
DETAIL = BASE + "/home/2-2/{nid}"

PAGES = 12                       # ≈120 most recent announcements (about 9 months)
_DOC_WANTED = re.compile(r"공고|안내서|모집|공모")


def fetch(deep: bool = True, pages: int = PAGES) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        try:
            markup = get_text(LIST.format(page=page))
        except RuntimeError:
            break
        rows = _parse_list(markup)
        if not rows:
            break
        for row in rows:
            if row["nid"] in seen or not E.is_gpu_program(row["title"], row["program"]):
                continue
            seen.add(row["nid"])
            out.append(_build(row, deep))
    return out


def _parse_list(markup: str) -> list[dict]:
    rows = []
    markup = re.sub(r"<!--.*?-->", " ", markup, flags=re.S)   # rows carry commented-out labels
    for tr in re.findall(r"<tr>(.*?)</tr>", markup, re.S):
        link = re.search(r'<a href="/home/2-2/(\d+)"[^>]*>(.*?)</a>', tr, re.S)
        if not link:
            continue
        title = _html.unescape(re.sub(r"<[^>]+>", " ", link.group(2)))
        title = re.sub(r"\s+", " ", title).strip()
        program = re.search(r'class="box bluebox">(.*?)</span>', tr, re.S)
        program = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", program.group(1)))).strip() \
            if program else ""
        flat = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", tr)))
        period = re.search(r"신청기간\s*:\s*([\d\-:. ]+~[\d\-:. ]+)", flat)
        posted = re.findall(r"(\d{4}-\d{2}-\d{2})", flat)
        rows.append({
            "nid": link.group(1),
            "title": title,
            "program": program,
            "period": period.group(1).strip() if period else "",
            "posted": posted[-1] if posted else "",
        })
    return rows


def _build(row: dict, deep: bool) -> dict:
    url = DETAIL.format(nid=row["nid"])
    start = end = None
    if row["period"]:
        left, _, right = row["period"].partition("~")
        start, end = parse_dt(left), parse_dt(right)

    body, attachments, doc = "", [], ""
    if deep:
        body, attachments, doc = _detail(url)
    blob = "\n".join(x for x in (row["title"], body, doc) if x)

    return {
        "source": SOURCE,
        "source_label": SOURCE_LABEL,
        "org": ORG,
        "uid": f"{SOURCE}-{row['nid']}",
        "title": row["title"],
        "program": row["program"],
        "url": url,
        "posted": row["posted"],
        "apply_start": start,
        "apply_end": end,
        "closed_hint": bool(re.search(r"접수\s*마감|점수마감|모집\s*종료", row["title"])),
        "audience": E.audience(row["title"], row["program"]),
        "gpu_models": E.find_models(blob),
        "gpu_specs": E.find_specs(blob),
        "scale": E.find_scale(blob),
        "cost": E.find_cost(blob),
        "usage_period": E.find_usage_period(blob),
        "summary": _summary(body),
        "attachments": attachments,
    }


def _detail(url: str) -> tuple[str, list[dict], str]:
    try:
        page = get_text(url)
    except RuntimeError:
        return "", [], ""
    # the board wraps the post body in the page; cut the chrome off around it
    inner = page
    m = re.search(r"(내용|본문)\s*</\w+>(.*?)(첨부파일|<footer)", page, re.S)
    if m:
        inner = m.group(2)
    body = html_to_text(inner)
    body = re.sub(r"^.*?(?=공고|과학기술정보통신부|사업)", "", body, count=1, flags=re.S)[:8000]

    found: list[dict] = []
    for href, label in re.findall(r'<a[^>]+href="(/comm/getFile[^"]*)"[^>]*>(.*?)</a>', page, re.S | re.I):
        name = re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", "", label))).strip()
        name = re.sub(r"\s*\(파일크기:.*$", "", name)
        found.append({"name": name, "url": BASE + _html.unescape(href)})

    texts = []
    for att in found:
        ext = re.search(r"\.(hwpx|hwp|pdf)$", att["name"], re.I)
        if not ext or not _DOC_WANTED.search(att["name"]):
            continue
        path = download(att["url"], referer=url, suffix="." + ext.group(1).lower())
        if path:
            texts.append(doc_text(path, att["name"]))
        if len(texts) >= 3:
            break
    return body, found, "\n".join(t for t in texts if t)


def _summary(body: str) -> str:
    for line in body.split("\n"):
        line = line.strip()
        if len(line) > 40 and "공고" not in line[:6]:
            return line[:180]
    return ""
