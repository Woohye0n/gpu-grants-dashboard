#!/usr/bin/env python
"""AI 사용량 스냅샷(dashboard.json)을 원본 저장소에서 받아 docs/ai/data/ 에 둔다.

프론트(docs/ai/app.js)는 평소 raw.githubusercontent 에서 직접 읽고, 실패하면
./data/dashboard.json 로 내려온다. 그 사본을 최신으로 유지하는 역할이다.

원본 저장소가 private 으로 바뀌면 브라우저는 raw 를 못 읽으므로 **이 사본이 유일한
데이터원이 된다.** 그때는 토큰이 있어야 하므로 GH_TOKEN 을 읽는다.

    python -m scraper.sync_ai_snapshot
    GH_TOKEN=... python -m scraper.sync_ai_snapshot     # private 저장소
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from .common import ROOT, now_kst

REPO = os.environ.get("AI_SNAPSHOT_REPO", "AIDASLab/aidas-ai-monitoring-dashboard")
SRC_PATH = "data/dashboard.json"
OUT = os.path.join(ROOT, "docs", "ai", "data", "dashboard.json")


def fetch(repo: str, token: str = "") -> bytes:
    """공개면 raw 로, private 이면 contents API 로 받는다."""
    attempts = [(f"https://raw.githubusercontent.com/{repo}/main/{SRC_PATH}", {})]
    if token:
        attempts.append((
            f"https://api.github.com/repos/{repo}/contents/{SRC_PATH}?ref=main",
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.raw"}))
    last = None
    for url, headers in attempts:
        req = urllib.request.Request(url, headers={"User-Agent": "gpu-grants-dashboard", **headers})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as exc:                          # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
    raise RuntimeError(f"스냅샷을 받지 못했습니다 ({repo}): {last}")


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 사용량 스냅샷 동기화")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    try:
        blob = fetch(a.repo, token)
        payload = json.loads(blob)                        # 형태 확인 후에만 덮어쓴다
    except Exception as exc:                              # noqa: BLE001
        have = os.path.exists(a.out)
        print(f"[warn] {exc}", file=sys.stderr)
        print(f"[warn] {'기존 사본을 유지합니다' if have else '사본이 없습니다'}", file=sys.stderr)
        return 0 if have else 1

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "wb") as fh:
        fh.write(blob)
    gen = payload.get("generated_at")
    when = ""
    if isinstance(gen, (int, float)):
        from datetime import datetime, timezone
        when = datetime.fromtimestamp(gen / 1000, timezone.utc).astimezone(
            now_kst().tzinfo).strftime("%Y-%m-%d %H:%M KST")
    print(f"[{now_kst():%Y-%m-%d %H:%M} KST] 스냅샷 {len(blob)/1024:.0f}KB "
          f"(생성 {when or '?'}) → {os.path.relpath(a.out, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
