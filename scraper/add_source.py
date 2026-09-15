"""이슈 본문의 설정 JSON 을 검사해 sources.json 에 추가한다.

'사이트 추가' 폼이 연 GitHub 이슈를 워크플로가 이 스크립트에 넘긴다.
검사는 실제로 그 주소를 읽어보는 것이다 — 못 읽으면 추가하지 않고 이유를 남긴다.

    python -m scraper.add_source --body-file issue.md --report-file report.md
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import sys
import urllib.parse

from .common import ROOT

SOURCES_FILE = os.path.join(ROOT, "sources.json")
ALLOWED_KEYS = {"key", "kind", "label", "org", "list_url", "page_param", "pages",
                "detail_url", "program", "disabled"}
REQUIRED = ("label", "list_url")


class Rejected(Exception):
    """추가할 수 없는 이유. 그대로 이슈 댓글이 된다."""


def extract_config(body: str) -> dict:
    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", body or "", re.S)
    if not block:
        raise Rejected("이슈 본문에서 ```json 설정 블록을 찾지 못했습니다.")
    try:
        cfg = json.loads(block.group(1))
    except ValueError as exc:
        raise Rejected(f"설정 JSON 을 읽지 못했습니다: {exc}") from exc
    if not isinstance(cfg, dict):
        raise Rejected("설정은 JSON 객체여야 합니다.")
    return cfg


def validate(cfg: dict) -> dict:
    unknown = set(cfg) - ALLOWED_KEYS
    if unknown:
        raise Rejected(f"알 수 없는 항목: {', '.join(sorted(unknown))}")
    for field in REQUIRED:
        if not str(cfg.get(field) or "").strip():
            raise Rejected(f"`{field}` 은 필수입니다.")

    url = cfg["list_url"].strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise Rejected("목록 URL 은 http/https 여야 합니다.")
    _reject_internal(parsed.hostname or "")

    clean = {
        "key": _make_key(cfg, parsed),
        "kind": "generic",
        "label": str(cfg["label"]).strip()[:80],
        "list_url": url,
    }
    if cfg.get("org"):
        clean["org"] = str(cfg["org"]).strip()[:80]
    if cfg.get("program"):
        clean["program"] = str(cfg["program"]).strip()[:120]
    if cfg.get("page_param"):
        param = str(cfg["page_param"]).strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,40}", param):
            raise Rejected("페이지 파라미터 이름이 이상합니다.")
        clean["page_param"] = param
    pages = int(cfg.get("pages") or 1)
    clean["pages"] = max(1, min(pages, 20))
    if cfg.get("detail_url"):
        detail = str(cfg["detail_url"]).strip()
        d = urllib.parse.urlparse(detail)
        if d.scheme not in ("http", "https"):
            raise Rejected("상세 URL 은 http/https 여야 합니다.")
        _reject_internal(d.hostname or "")
        if "{id}" not in detail:
            raise Rejected("상세 URL 에는 글 번호 자리인 `{id}` 가 있어야 합니다.")
        clean["detail_url"] = detail
    return clean


def _reject_internal(host: str) -> None:
    """러너 안쪽이나 사설망을 겨누는 주소는 받지 않는다."""
    if not host:
        raise Rejected("주소에 호스트가 없습니다.")
    if host.lower() in ("localhost", "localhost.localdomain") or host.endswith(".internal"):
        raise Rejected("내부 주소는 등록할 수 없습니다.")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise Rejected(f"주소를 찾을 수 없습니다 ({host}): {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise Rejected(f"내부/사설 IP 로 해석되는 주소입니다 ({host} -> {ip}).")


def _make_key(cfg: dict, parsed) -> str:
    raw = str(cfg.get("key") or parsed.hostname or "site")
    key = re.sub(r"[^a-z0-9_]+", "_", raw.lower().replace("www.", "")).strip("_")[:32]
    return key or "site"


def check_reachable(cfg: dict) -> dict:
    """실제로 읽어본다. 이게 이 스크립트의 핵심이다."""
    from .probe import probe, suggest_boards
    try:
        found = probe(cfg["list_url"])
    except Exception as exc:                             # noqa: BLE001
        raise Rejected(f"목록을 열지 못했습니다: {type(exc).__name__}: {exc}") from exc
    if found.get("shape", {}).get("challenge"):
        raise Rejected(
            "이 사이트가 **자동 수집을 막고 있습니다.** 사람 확인(자동등록방지) 페이지가 대신 내려옵니다.\n\n"
            f"받은 페이지: {found['shape'].get('html_chars', 0)}자 — "
            f"\"{found['shape'].get('text_head', '')[:90]}\"\n\n"
            "사이트가 의도적으로 세운 접근 통제라 우회하지 않습니다. "
            "이 사이트는 GitHub 러너에서 수집할 수 없고, 해당 사이트가 정상 응답하는 망에서 "
            "수집해 올리는 방법만 가능합니다.")
    if not found["row_count"]:
        shape = found.get("shape") or {}
        lines = ["목록은 열렸지만 게시글 줄을 찾지 못했습니다."]
        lines.append("")
        lines.append(f"받은 페이지: {shape.get('html_chars', 0)}자, 링크 {shape.get('links', 0)}개, "
                     f"표 {shape.get('tables', 0)}개")
        if shape.get("html_chars", 0) < 3000 and shape.get("links", 0) < 5:
            lines.append("")
            lines.append("**껍데기만 내려오는 자바스크립트 앱**으로 보입니다. 브라우저에서는 목록이 보여도 "
                         "서버가 주는 HTML 에는 글이 없어서, 이 방식으로는 읽을 수 없습니다.")
        if not urllib.parse.urlparse(cfg["list_url"]).path.strip("/"):
            lines.append("")
            lines.append("넣어주신 건 **사이트 첫 화면** 주소입니다. "
                         "공지사항·사업공고 게시판으로 들어가서, "
                         "공고가 여러 줄 보이는 **목록 페이지**의 주소를 넣어주세요.")
        hints = suggest_boards(cfg["list_url"])
        if hints:
            lines.append("")
            lines.append("이 사이트에서 게시판으로 보이는 주소들입니다. 이 중 하나를 넣어보세요:")
            lines.append("")
            for h in hints:
                lines.append(f"- [{h['text']}]({h['url']})")
        else:
            lines.append("")
            lines.append("상세 페이지나, 목록을 자바스크립트로만 그리는 페이지는 읽을 수 없습니다.")
        raise Rejected("\n".join(lines))
    return found


def _existing(cfg: dict) -> dict | None:
    """같은 키나 같은 목록 주소가 이미 sources.json 에 있나."""
    try:
        with open(SOURCES_FILE, encoding="utf-8") as fh:
            rows = json.load(fh).get("sources") or []
    except (OSError, ValueError):
        return None
    for row in rows:
        if row.get("key") == cfg["key"] or row.get("list_url") == cfg["list_url"]:
            return row
    return None


def add(cfg: dict) -> tuple[bool, dict]:
    with open(SOURCES_FILE, encoding="utf-8") as fh:
        conf = json.load(fh)
    rows = conf.setdefault("sources", [])
    for i, existing in enumerate(rows):
        if existing.get("key") == cfg["key"] or existing.get("list_url") == cfg["list_url"]:
            rows[i] = {**existing, **cfg}
            _write(conf)
            return False, cfg
    rows.append(cfg)
    _write(conf)
    return True, cfg


def _write(conf: dict) -> None:
    with open(SOURCES_FILE, "w", encoding="utf-8") as fh:
        json.dump(conf, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--report-file", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.body_file, encoding="utf-8") as fh:
        body = fh.read()

    lines: list[str] = []
    try:
        cfg = validate(extract_config(body))
        already = _existing(cfg)
        if already:
            print_only = (f"ℹ️ **{already.get('label') or cfg['label']}** 는 이미 수집 대상입니다 "
                          f"(키 `{already['key']}`). 새로 추가할 것이 없습니다.")
            if args.report_file:
                with open(args.report_file, "w", encoding="utf-8") as fh:
                    fh.write(print_only)
            print(print_only)
            return 0
        found = check_reachable(cfg)
        titles = "\n".join(f"  - {r['title'][:70]}" for r in found["rows"][:5])
        if args.dry_run:
            lines.append(f"검사 통과 (게시글 {found['row_count']}줄 읽음)\n{titles}")
            ok = True
        else:
            added, cfg = add(cfg)
            ok = True
            lines.append(f"✅ **{cfg['label']}** 을 수집 대상에 {'추가' if added else '갱신'}했습니다.")
            lines.append("")
            lines.append(f"- 목록에서 게시글 **{found['row_count']}줄** 을 읽었습니다.")
            lines.append(f"- 등록 키: `{cfg['key']}` · 훑는 페이지: {cfg.get('pages', 1)}")
            lines.append("")
            lines.append("읽어본 제목 몇 개:")
            lines.append(titles)
            lines.append("")
            lines.append("다음 수집(매일 09:10 KST)부터 이 사이트도 함께 훑습니다. "
                         "GPU·고성능컴퓨팅 관련 공고만 대시보드에 올라옵니다.")
    except Rejected as exc:
        ok = False
        lines.append(f"❌ 추가하지 못했습니다.\n\n{exc}")
    except Exception as exc:                             # noqa: BLE001
        ok = False
        lines.append(f"❌ 처리 중 오류: `{type(exc).__name__}: {exc}`")

    report = "\n".join(lines)
    print(report)
    if args.report_file:
        with open(args.report_file, "w", encoding="utf-8") as fh:
            fh.write(report)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
