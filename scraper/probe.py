"""게시판 목록 URL 하나로 구조를 추정한다.

사이트마다 마크업이 달라 손으로 모듈을 짜 왔지만, 국내 공공기관 게시판은
'같은 모양의 행이 반복되고 각 행에 제목 링크와 날짜가 있다'는 공통점이 크다.
그 반복 구조를 찾아 목록을 읽어내고, 못 읽으면 못 읽었다고 말한다.

    python -m scraper.probe <목록 URL>
"""
from __future__ import annotations

import re
import sys
import urllib.parse
from collections import defaultdict

from bs4 import BeautifulSoup

from .common import get_text

DATE_RE = re.compile(r"(20\d{2})[.\-/년]\s*(\d{1,2})[.\-/월]\s*(\d{1,2})")
# 목록 행이 아닌 링크들 (메뉴·푸터·페이징)
SKIP_TEXT = re.compile(r"^(다음|이전|처음|마지막|목록|더보기|검색|로그인|회원가입|홈|\d+)$")
SKIP_HREF = re.compile(r"^(#|javascript:|mailto:|tel:)", re.I)
# 제목 뒤에 붙는 목록 메타 (첨부파일 있음 / new / 2026.09.11 / 조회 123)
TITLE_TAIL = re.compile(
    r"(\s*(첨부파일\s*있음|첨부|new|N|HOT|공지|마감|진행중|접수중))+\s*$|"
    r"\s*20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}.*$|\s*조회\s*[\d,]+.*$", re.I)


def _signature(a) -> str:
    """링크가 놓인 자리를 태그/클래스 경로로 요약한다. 같은 목록 행은 같은 값이 나온다."""
    parts = []
    node = a
    for _ in range(4):
        node = node.parent
        if node is None or not getattr(node, "name", None):
            break
        cls = ".".join(sorted(node.get("class") or []))
        parts.append(f"{node.name}{('.' + cls) if cls else ''}")
    return ">".join(reversed(parts))


def _clean_title(node) -> str:
    """행 전체 텍스트에서 제목만 남긴다. 제목 전용 자식이 있으면 그쪽을 쓴다."""
    for sel in ("a", "p", "strong", ".title", ".subject", ".tit"):
        child = node.select_one(sel)
        if child is not None:
            text = child.get_text(" ", strip=True)
            if 6 <= len(text) <= 160:
                return TITLE_TAIL.sub("", text).strip()
    text = node.get_text(" ", strip=True)
    prev = None
    while prev != text:                                  # 메타가 여러 개 붙어 있을 수 있다
        prev, text = text, TITLE_TAIL.sub("", text).strip()
    return text


def _row_text(a) -> str:
    node = a
    for _ in range(4):
        node = node.parent
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        if len(text) > len(a.get_text(strip=True)) + 8:
            return text
    return a.get_text(" ", strip=True)


def probe(url: str, min_rows: int = 5) -> dict:
    html = get_text(url)
    soup = BeautifulSoup(html, "lxml")
    groups: dict[str, list] = defaultdict(list)

    # <a> 뿐 아니라 onclick 으로 상세를 여는 행도 본다 (goDetail('...') 류 ASP 게시판)
    candidates = list(soup.select("a")) + [
        n for n in soup.select("[onclick]") if n.name != "a"]
    for a in candidates:
        title = a.get_text(" ", strip=True)
        href = (a.get("href") or "").strip()
        onclick = (a.get("onclick") or "") + (a.get("href") or "")
        if not title or len(title) < 6 or len(title) > 160 or SKIP_TEXT.match(title):
            continue
        # 자바스크립트로 여는 게시판(goDetail('...')) 도 식별자만 있으면 받는다
        # goDetail('000...'), go_view(179192,78336) 처럼 식별자만 주는 게시판도 받는다.
        # 인자가 여럿이면 어느 것이 글 번호인지는 나중에 '행마다 달라지는 쪽'으로 고른다.
        args = re.findall(r"""['"(,\s](\d{4,})['")\,]""", onclick)
        if SKIP_HREF.match(href) and not args:
            continue
        groups[_signature(a)].append(
            (a, _clean_title(a) or title, "" if SKIP_HREF.match(href) else href, args))

    best, rows = "", []
    for sig, items in groups.items():
        dated = [i for i in items if DATE_RE.search(_row_text(i[0]))]
        if len(items) >= min_rows and len(dated) >= len(items) * 0.5 and len(items) > len(rows):
            best, rows = sig, items

    # 게시판 번호는 모든 행에서 같고 글 번호만 달라진다 — 가장 많이 달라지는 자리를 고른다
    width = max((len(r[3]) for r in rows), default=0)
    id_pos, best_variety = 0, -1
    for pos in range(width):
        variety = len({r[3][pos] for r in rows if len(r[3]) > pos})
        if variety > best_variety:
            id_pos, best_variety = pos, variety

    detected = []
    for a, title, href, args in rows:
        jsid = args[id_pos] if len(args) > id_pos else (args[0] if args else "")
        row_text = _row_text(a)
        m = DATE_RE.search(row_text)
        detected.append({
            "title": title,
            "url": urllib.parse.urljoin(url, href) if href and not SKIP_HREF.match(href) else "",
            "js_id": jsid,
            "posted": f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else "",
            "row_text": row_text[:200],
        })
    return {"url": url, "signature": best, "id_arg_index": id_pos,
            "row_count": len(detected), "rows": detected,
            # 못 읽었을 때 '어떤 페이지가 왔는지' 를 남긴다. 같은 주소라도 요청한
            # 곳(국내/해외 IP)에 따라 다른 페이지가 오는 일이 있다.
            "shape": {"html_chars": len(html), "links": len(soup.select("a")),
                      "tables": len(soup.select("table")),
                      "text_head": re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:160],
                      "challenge": bool(CHALLENGE.search(
                          re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:2000]))}}


# 사이트가 자동 수집을 막을 때 내려주는 페이지들. 우회하지 않고, 그렇다고 말한다.
CHALLENGE = re.compile(
    r"자동등록방지|보안절차|prove that you are human|captcha|are you a robot"
    r"|비정상적인 접근|접근이 차단|access denied|attention required", re.I)


BOARD_WORDS = re.compile(r"공지|공고|알림|소식|게시|보도|뉴스|사업안내|모집|notice|board|bbs", re.I)


def suggest_boards(url: str, limit: int = 8) -> list[dict]:
    """목록을 못 읽었을 때, 이 사이트 안에서 게시판처럼 보이는 주소를 찾아준다.

    사람들이 흔히 사이트 첫 화면 주소를 넣기 때문이다 — 거기서 공지사항·사업공고로
    가는 링크를 짚어주면 한 번에 끝난다.
    """
    try:
        soup = BeautifulSoup(get_text(url), "lxml")
    except Exception:                                    # noqa: BLE001
        return []
    host = urllib.parse.urlparse(url).netloc
    out, seen = [], set()
    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True)
        href = urllib.parse.urljoin(url, a["href"])
        if urllib.parse.urlparse(href).netloc != host or href in seen:
            continue
        if not (BOARD_WORDS.search(text) or BOARD_WORDS.search(href)):
            continue
        if SKIP_HREF.match(a["href"]) or len(text) > 30:
            continue
        seen.add(href)
        out.append({"text": text or href, "url": href})
        if len(out) >= limit:
            break
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    try:
        result = probe(sys.argv[1])
    except Exception as exc:                             # noqa: BLE001 - CLI 는 이유만 알려주면 된다
        print(f"읽지 못했습니다: {type(exc).__name__}: {exc}")
        print("목록(게시판 리스트) 페이지 URL 인지, 로그인이 필요한 페이지는 아닌지 확인해 주세요.")
        return 1
    print(f"목록 URL : {result['url']}")
    print(f"행 구조   : {result['signature'] or '찾지 못함'}")
    print(f"읽은 행   : {result['row_count']}개\n")
    if not result["row_count"]:
        hints = suggest_boards(sys.argv[1])
        if hints:
            print("게시판으로 보이는 주소들 (이 중 하나를 넣어보세요):")
            for h in hints:
                print(f"  - {h['text'][:24]:26s} {h['url']}")
        return 1
    for row in result["rows"][:12]:
        link = row["url"] or (f"(JS id {row['js_id']})" if row["js_id"] else "(링크 없음)")
        print(f"  [{row['posted'] or '날짜?':10s}] {row['title'][:62]}")
        print(f"      {link[:110]}")
    return 0 if result["row_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
