"""sources.json 에 적힌 설정만으로 게시판을 읽는 수집기.

손으로 짠 모듈(aiinfrahub/nipa/seoulaihub)이 각 사이트의 별난 점을 다룬다면,
이쪽은 '반복되는 행 + 제목 링크 + 날짜' 라는 흔한 게시판 모양을 probe 로 읽는다.
새 사이트를 코드 수정 없이 붙이기 위한 통로다.
"""
from __future__ import annotations

import html as _html
import re
import urllib.parse

from .. import extract as E
from bs4 import BeautifulSoup

from ..common import download, doc_text, get_text, html_to_text, out_of_time
from ..probe import probe

# 목록에서 실제로 몇 줄을 읽었는지. 0건이 '못 읽음'인지 '해당 공고 없음'인지 가른다.
LAST_SCAN: dict[str, int] = {}

# 첨부는 확장자가 링크 주소가 아니라 링크 '글자' 에만 있는 경우가 흔하다
# (예: boardDownload.php?fileIdx=2135 → 글자는 '…공고문.pdf').
_DOC_EXT = re.compile(r"\.(hwpx|hwp|pdf)(?:\b|$)", re.I)


def fetch_config(cfg: dict, deep: bool = True) -> list[dict]:
    key = cfg["key"]
    label = cfg.get("label") or key
    pages = int(cfg.get("pages") or 1)
    out: list[dict] = []
    seen: set[str] = set()
    scanned = 0

    for page in range(1, pages + 1):
        if out_of_time():
            break
        list_url = _page_url(cfg, page)
        try:
            found = probe(list_url)
        except Exception:                                # noqa: BLE001 - 한 페이지 실패가 전체를 막지 않는다
            break
        if not found["row_count"]:
            break
        scanned += found["row_count"]
        for row in found["rows"]:
            if out_of_time():
                break
            url = row["url"] or _detail_url(cfg, row["js_id"])
            ident = row["js_id"] or url
            if not url or ident in seen:
                continue
            if not E.is_gpu_program(row["title"]):
                continue
            seen.add(ident)
            out.append(_build(cfg, label, ident, row, url, deep))
    LAST_SCAN[key] = scanned
    if not scanned:
        raise RuntimeError("목록에서 게시글을 한 줄도 읽지 못했습니다 "
                           "(목록 URL 이 맞는지, 로그인이 필요한 페이지는 아닌지 확인하세요)")
    return out


def _page_url(cfg: dict, page: int) -> str:
    base = cfg["list_url"]
    param = cfg.get("page_param")
    if page == 1 or not param:
        return base
    joiner = "&" if "?" in base else "?"
    return f"{base}{joiner}{param}={page}"


def _detail_url(cfg: dict, ident: str) -> str:
    template = cfg.get("detail_url")
    return template.replace("{id}", ident) if (template and ident) else ""


def _build(cfg: dict, label: str, ident: str, row: dict, url: str, deep: bool) -> dict:
    body, attachments, doc = ("", [], "")
    if deep:
        body, attachments, doc = _detail(url)
    blob = "\n".join(x for x in (row["title"], body, doc) if x)
    start, end = E.find_apply_period(blob, fallback_year=(row.get("posted") or "")[:4] or None)

    return {
        "source": cfg["key"],
        "source_label": label,
        "org": cfg.get("org") or label,
        "uid": f"{cfg['key']}-{re.sub(r'[^A-Za-z0-9_-]', '', ident)[:40]}",
        "title": row["title"],
        "program": cfg.get("program") or label,
        "url": url,
        "posted": row.get("posted") or "",
        "apply_start": start,
        "apply_end": end,
        "closed_hint": bool(re.search(r"접수\s*마감|모집\s*종료|마감|선정\s*결과", row["title"])),
        "audience": E.audience(row["title"], body[:2000]),
        "gpu_models": E.find_models(blob),
        "unknown_models": E.find_unknown_models(blob),
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
    except Exception:                                    # noqa: BLE001
        return "", [], ""
    body = html_to_text(page)[:20000]

    found: list[dict] = []
    try:
        soup = BeautifulSoup(page, "lxml")
    except Exception:                                    # noqa: BLE001
        soup = None
    for a in (soup.select("a[href]") if soup else []):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if href.lower().startswith(("javascript:", "mailto:", "#")):
            continue
        if not (_DOC_EXT.search(text) or _DOC_EXT.search(href)):
            continue
        full = urllib.parse.urljoin(url, _html.unescape(href))
        if full in {x["url"] for x in found}:
            continue
        found.append({"name": (text or full.rsplit("/", 1)[-1])[:160], "url": full})

    texts = []
    for att in found:
        ext = _DOC_EXT.search(att["name"]) or _DOC_EXT.search(att["url"])
        if not ext:
            continue
        path = download(att["url"], referer=url, suffix="." + ext.group(1).lower())
        if path:
            texts.append(doc_text(path, att["name"]))
        if len(texts) >= 2:
            break
    return body, found, "\n".join(t for t in texts if t)


def _summary(body: str) -> str:
    for line in body.split("\n"):
        line = line.strip()
        if len(line) > 45:
            return line[:200]
    return ""
