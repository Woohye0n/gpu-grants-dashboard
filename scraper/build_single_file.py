"""docs/ 를 한 파일짜리 HTML 로 묶습니다 (메일 첨부·오프라인 보관·Artifact 게시용).

    python -m scraper.build_single_file [출력경로]
    python -m scraper.build_single_file --artifact [출력경로]   # 문서 골격 없는 조각
"""
from __future__ import annotations

import os
import re
import sys

from .common import ROOT

DOCS = os.path.join(ROOT, "docs")
_REFRESH_BTN = re.compile(r'\s*<button id="refreshBtn".*?</button>', re.S)


def build(out_path: str | None = None) -> str:
    """index.html + style.css + app.js + data.js -> 한 파일."""
    html = _read("index.html")
    css, js, data = _read("style.css"), _read("app.js"), _read("data.js")

    html = _sub(r'\s*<link rel="stylesheet"[^>]*>', f"\n  <style>\n{css}\n  </style>", html)
    html = _sub(r'\s*<script src="\./data\.js[^"]*"[^>]*></script>', "", html)
    html = _sub(r'\s*<script src="\./app\.js[^"]*"[^>]*></script>', "", html)
    # the bundled script must run after the markup it wires up, so it goes last
    html = _sub(r"</body>", f"<script>\n{data}\n{js}\n</script>\n</body>", html)
    # 새로고침은 옆에 data.json 이 있어야 동작합니다
    html = _REFRESH_BTN.sub("", html, count=1)

    out_path = out_path or os.path.join(DOCS, "standalone.html")
    _write(out_path, html)
    return out_path


def build_artifact(out_path: str | None = None) -> str:
    """Artifact 게시용: <!doctype>/<html>/<head>/<body> 는 게시 시점에 씌워지므로
    title + style + 본문 + script 만 남깁니다."""
    tmp = build(os.path.join(DOCS, ".tmp_standalone.html"))
    html = _read(os.path.basename(tmp))
    os.remove(tmp)

    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)
    script = re.search(r"<script>(.*?)</script>", html, re.S)
    body_html = body.replace(script.group(0), "") if script else body

    stamp = re.search(r'"generated_at_kst": "([^"]+)"', html)
    if stamp:
        body_html = body_html.replace(
            "</footer>",
            f"<br />이 페이지는 <b>{stamp.group(1)} KST</b> 스냅샷입니다 — "
            "매일 갱신되는 쪽은 서버에서 도는 대시보드입니다.</footer>")

    out_path = out_path or os.path.join(DOCS, "artifact.html")
    _write(out_path, f"<title>{title}</title>\n<style>\n{style}\n</style>\n"
                     f"{body_html.strip()}\n<script>\n{script.group(1) if script else ''}\n</script>\n")
    return out_path


def _sub(pattern: str, replacement: str, text: str) -> str:
    """re.sub with a literal replacement — CSS/JS is full of backslashes."""
    return re.sub(pattern, lambda _m: replacement, text, count=1, flags=re.S)


def _read(name: str) -> str:
    with open(os.path.join(DOCS, name), encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--artifact"]
    fn = build_artifact if "--artifact" in sys.argv else build
    print(fn(args[0] if args else None))
