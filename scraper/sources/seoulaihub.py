"""서울 AI 허브 (seoulaihub.kr) — 허브소식 > 공지사항.

Classic ASP board: rows are <ul> blocks whose title cell carries
goDetail('<bd_num>','bd_num'). Detail pages hold the whole announcement inline,
including the GPU / VRAM / per-pod table, so no attachment parsing is needed.
"""
from __future__ import annotations

import html as _html
import re
import urllib.parse

from .. import extract as E
from ..common import out_of_time, download, doc_text, get_text, html_to_text, parse_dt

SOURCE = "seoulaihub"
SOURCE_LABEL = "서울 AI 허브"
ORG = "서울특별시 · 서울 AI 허브"
BASE = "https://www.seoulaihub.kr"
BOARD = "/board/board_basic/board_list.asp?scrID=0000000170&pageNum=4&subNum=1&ssubNum=1&page={page}"
DETAIL = ("/board/board_basic/board_detail.asp?scrID=0000000170&pageNum=4&subNum=1&ssubNum=1"
          "&page=1&bd_num={bd}&act=view&s_string=")

PAGES = 3                        # ≈60 most recent notices
_CANDIDATE = re.compile(r"모집|지원\s*사업|지원사업|신청|인프라|컴퓨팅|GPU", re.I)
_DOC_WANTED = re.compile(r"공고|안내|모집|붙임")
DOWNLOAD = BASE + "/include/download.asp?file_name={disp}&file_full_name={stored}&folder={folder}"


def fetch(deep: bool = True, pages: int = PAGES) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        if out_of_time():
            break
        try:
            markup = get_text(BASE + BOARD.format(page=page))
        except RuntimeError:
            break
        rows = _parse_list(markup)
        if not rows:
            break
        for row in rows:
            if out_of_time():
                break
            if row["bd"] in seen:
                continue
            seen.add(row["bd"])
            strong = E.is_gpu_program(row["title"])
            if not strong and not _CANDIDATE.search(row["title"]):
                continue
            item = _build(row, deep=deep or strong)
            # a title without 'GPU' still qualifies if the body is really about GPUs
            if strong or (item["_body"].count("GPU") >= 3 and E.is_gpu_program(row["title"], item["_body"][:4000])):
                item.pop("_body")
                out.append(item)
    return out


def _parse_list(markup: str) -> list[dict]:
    rows = []
    for block in re.findall(r"<ul class=\"faq_board_sty03[^\"]*\">(.*?)</ul>", markup, re.S):
        bd = re.search(r"goDetail\('(\d+)'", block)
        title = re.search(r"<p>(.*?)</p>", block, re.S)
        if not bd or not title:
            continue
        cells = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                 for c in re.findall(r"<li[^>]*>(.*?)</li>", block, re.S)]
        posted = next((c for c in cells if re.fullmatch(r"\d{4}-\d{2}-\d{2}", c)), "")
        rows.append({
            "bd": bd.group(1),
            "title": re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", title.group(1)))).strip(),
            "posted": posted,
        })
    return rows


def _build(row: dict, deep: bool) -> dict:
    url = BASE + DETAIL.format(bd=row["bd"])
    body, attachments, doc = ("", [], "")
    if deep:
        body, attachments, doc = _detail(url)
    blob = "\n".join(x for x in (row["title"], body, doc) if x)
    year = (row["posted"] or "")[:4] or None
    start, end = E.find_apply_period(blob, fallback_year=year)
    if not end:                                          # titles often carry '(~9/16)'
        m = re.search(r"[~∼]\s*(\d{1,2})\s*[./]\s*(\d{1,2})", row["title"])
        if m and year:
            end = parse_dt(f"{year}.{m.group(1)}.{m.group(2)}")

    return {
        "source": SOURCE,
        "source_label": SOURCE_LABEL,
        "org": ORG,
        "uid": f"{SOURCE}-{row['bd']}",
        "title": row["title"],
        "program": _program(row["title"]),
        "url": url,
        "posted": row["posted"],
        "apply_start": start,
        "apply_end": end,
        "closed_hint": bool(re.search(r"마감|종료|선정\s*결과", row["title"])),
        "audience": E.audience(row["title"], body[:2000]),
        "gpu_models": E.find_models(blob),
        "unknown_models": E.find_unknown_models(blob),
        "gpu_specs": E.find_specs(blob),
        "scale": E.find_scale(blob),
        "cost": E.find_cost(blob),
        "usage_period": E.find_usage_period(blob),
        "summary": _summary(body),
        "attachments": attachments,
        "_body": body,
    }


def _detail(url: str) -> tuple[str, list[dict], str]:
    try:
        page = get_text(url)
    except RuntimeError:
        return "", [], ""
    text = html_to_text(page)
    # cut the site chrome: the post starts at the notice title, ends at the nav footer
    start = text.find("> 허브소식 > 공지사항")
    if start > 0:
        text = text[start + 14:]
    for marker in ("다음글", "목록보기", "개인정보처리방침"):
        idx = text.find(marker)
        if idx > 400:
            text = text[:idx]
            break
    return text.strip()[:20000], *_attachments(page, url)


def _attachments(page: str, referer: str) -> tuple[list[dict], str]:
    """goDown('<표시이름>','<저장이름>','<게시판>') -> /include/download.asp"""
    found: list[dict] = []
    for disp, stored, folder in re.findall(
            r"goDown\('([^']+)'\s*,\s*'([^']+)'\s*,\s*'([^']+)'\)", page):
        url = DOWNLOAD.format(disp=urllib.parse.quote(_html.unescape(disp)),
                              stored=urllib.parse.quote(stored), folder=folder)
        entry = {"name": _html.unescape(disp), "url": url}
        if entry not in found:
            found.append(entry)
    texts = []
    for att in found:
        ext = re.search(r"\.(hwpx|hwp|pdf)$", att["name"], re.I)
        if not ext or not _DOC_WANTED.search(att["name"]):
            continue
        path = download(att["url"], referer=referer, suffix="." + ext.group(1).lower())
        if path:
            texts.append(doc_text(path, att["name"]))
        if len(texts) >= 2:
            break
    return found, "\n".join(t for t in texts if t)


def _program(title: str) -> str:
    m = re.search(r"[「『]([^」』]+)[」』]", title)
    if m:
        return m.group(1)
    m = re.search(r"((?:[가-힣A-Za-z0-9·X×\s]+?)\s*지원\s*사업)", title)
    return m.group(1).strip() if m else "서울 AI 허브 공고"


def _summary(body: str) -> str:
    for line in body.split("\n"):
        line = line.strip()
        if len(line) > 45 and not line.startswith(("신청", "접수", "공고")):
            return line[:200]
    return ""
