"""국가 AI컴퓨팅자원 지원포털 (aiinfrahub.kr) — 사업 공고.

The list page is a jQuery app; it reads /api/projects, so we read that directly.
Announcement bodies are short, so the GPU numbers come from the .hwp attachments.
"""
from __future__ import annotations

import html as _html
import re
import urllib.parse

from .. import extract as E
from ..common import out_of_time, download, doc_text, get_json, get_text, html_to_text, iso_from_api

SOURCE = "aiinfrahub"
SOURCE_LABEL = "국가 AI컴퓨팅자원 지원포털"
ORG = "과기정통부·NIPA·KAIT"
BASE = "https://aiinfrahub.kr"
LIST_API = BASE + "/api/projects?searchType=projectName&searchText=&listSize=100&idx=0&postDate="
DETAIL = BASE + "/project/{pid}"

_DOC_WANTED = re.compile(r"공고문?|안내서|모집")


def fetch(deep: bool = True) -> list[dict]:
    data = get_json(LIST_API, referer=BASE + "/project")
    out: list[dict] = []
    for item in data.get("projectDataList", []):
        if out_of_time():
            break
        pid = str(item.get("id", "")).strip()
        title = (item.get("title") or "").strip()
        program = (item.get("projectName") or "").strip()
        if not pid or not E.is_gpu_program(title, program):
            continue
        url = DETAIL.format(pid=pid)
        body = html_to_text(item.get("content") or "")
        attachments, doc = [], ""
        if deep:
            attachments, doc = _attachments(url)
        blob = "\n".join(x for x in (body, doc) if x)

        out.append({
            "source": SOURCE,
            "source_label": SOURCE_LABEL,
            "org": ORG,
            "uid": f"{SOURCE}-{pid}",
            "title": title,
            "program": program,
            "url": url,
            "posted": (iso_from_api(item.get("postDate")) or "")[:10],
            "apply_start": iso_from_api(item.get("startDate")),
            "apply_end": iso_from_api(item.get("endDate")),
            "closed_hint": bool(re.search(r"접수\s*마감|점수마감|모집\s*종료|마감", title)),
            "audience": E.audience(title, program, body),
            "gpu_models": E.find_models(blob),
            "gpu_specs": E.find_specs(blob),
            "scale": E.find_scale(blob),
            "cost": E.find_cost(blob),
            "usage_period": E.find_usage_period(blob),
            "summary": _summary(body),
            "attachments": attachments,
        })
    return out


def _attachments(detail_url: str) -> tuple[list[dict], str]:
    """Attachment list + concatenated text of the announcement documents."""
    try:
        page = get_text(detail_url, referer=BASE + "/project")
    except RuntimeError:
        return [], ""
    found: list[dict] = []
    for name, aid in re.findall(
            r'data-filename="([^"]+)"[^>]*onclick="D\.fn\.file\.download\(&#39;/api/attach/(\d+)', page):
        found.append({"name": _html.unescape(name),
                      "url": f"{BASE}/api/attach/{aid}?fileName={urllib.parse.quote(_html.unescape(name))}"})
    texts = []
    for att in found:
        if not _DOC_WANTED.search(att["name"]) or not re.search(r"\.(hwpx?|pdf)$", att["name"], re.I):
            continue
        path = download(att["url"], referer=detail_url,
                        suffix=re.search(r"\.(hwpx?|pdf)$", att["name"], re.I).group(0).lower())
        if path:
            texts.append(doc_text(path, att["name"]))
        if len(texts) >= 3:
            break
    return found, "\n".join(t for t in texts if t)


def _summary(body: str) -> str:
    for line in body.split("\n"):
        line = line.strip(" \"'")
        if len(line) > 25 and not line.startswith(("공고번호", "제")):
            return line[:180]
    return ""
