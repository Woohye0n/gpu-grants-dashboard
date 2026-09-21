#!/usr/bin/env python
"""AI 사용량 스냅샷(dashboard.json)을 설정된 출처에서 받아 docs/ai/data/ 에 둔다.

화면(docs/ai/app.js)은 같은 출처의 ./data/dashboard.json 하나만 읽는다. 브라우저가
바깥으로 나가지 않으므로 **이 사본이 최신인지가 곧 화면의 신선도**다. 예전에는
브라우저가 스냅샷을 발행하는 다른 저장소를 직접 읽었는데, 그러면 이 사이트가 그
저장소 없이는 돌지 않았고, 화면이 멈췄을 때 발행이 멈춘 것인지 이쪽이 못 받아오는
것인지도 구분할 수 없었다.

`ai_snapshot.json` 이 `inbox` 를 가리키면 **이 저장소가 직접 만듭니다** — 송신기들이
NAS 에 떨군 배치를 읽어 집계합니다(scraper/ai_store.py + ai_bundle.py). 그게 없을
때만 이미 만들어진 스냅샷을 복사해 옵니다(`sources`: 파일 경로 또는 http(s) 주소).

왜 직접 만드나: 예전에는 다른 사람의 중앙 서버가 만들어 다른 깃헙 저장소로
발행하는 것을 받아 썼습니다. 2026-09-16 에 그 발행이 멈췄고, 수집기도 inbox 도
멀쩡한데 화면만 4.5일 과거에 멈춰 있었습니다. 중간에 남의 손이 필요한 칸이
있으면 언젠가 그 칸에서 멈춥니다.

결과는 사본 옆 `data/sync.json` 에 남겨, 화면이 무엇이 멈췄는지 말할 수 있게 한다.

    python -m scraper.sync_ai_snapshot            # inbox 에서 집계 (기본)
    python -m scraper.sync_ai_snapshot --rebuild  # DB 를 비우고 처음부터
    python -m scraper.sync_ai_snapshot --seed <dashboard.json>   # 누적을 이어받기
    python -m scraper.sync_ai_snapshot --source <경로|주소>      # 복사 모드 강제

종료 코드: 0 최신 / 1 못 만들었지만 사본은 있다(저하) / 2 사본도 없다(고장)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from . import ai_bundle, ai_store
from .common import KST, ROOT, now_kst

CONFIG = os.path.join(ROOT, "ai_snapshot.json")
OUT = os.path.join(ROOT, "docs", "ai", "data", "dashboard.json")
META = os.path.join(ROOT, "docs", "ai", "data", "sync.json")
TIMEOUT = 60


def load_config() -> dict:
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            conf = json.load(fh)
        return conf if isinstance(conf, dict) else {}
    except (OSError, ValueError):
        return {}


def build_from_inbox(conf: dict, out: str, meta_path: str, quiet: bool = False) -> int:
    """NAS inbox 를 읽어 스냅샷을 만든다. 이 경로에는 외부 의존이 없다."""
    inbox = os.path.expanduser(conf.get("inbox") or "")
    now_ms = int(time.time() * 1000)
    if not inbox or not os.path.isdir(inbox):
        update_meta(meta_path, checked_at=now_ms, ok=False, source=inbox or None,
                    error=f"inbox 를 읽을 수 없습니다: {inbox or '(설정 없음)'}")
        return 2 if not os.path.exists(out) else 1

    db = ai_store.connect()
    stats = ai_store.ingest_inbox(
        db, inbox, ignore_hosts=(conf.get("nas") or {}).get("ignore_hosts") or ())
    ai_store.prune(db, conf.get("retain_days", 30))
    bundle = ai_bundle.build(db, conf, now=now_ms)
    payload = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    write_atomic(out, payload)
    update_meta(meta_path, checked_at=now_ms, synced_at=now_ms, ok=True,
                source=inbox, error=None, mode="inbox", batches=stats["batches"])
    s = bundle["summary"]
    if stats["batches"] or not quiet:
        print(f"[{now_kst():%Y-%m-%d %H:%M} KST] 집계 완료 — 새 배치 {stats['batches']}개 "
              f"(전체 {stats['total_batches']}) · 계정 {len(s['accounts'])} · "
              f"세션 {len(bundle['sessions'])} · 라이브 {s['live_session_count']} · "
              f"{len(payload)/1024:.0f}KB → {os.path.relpath(out, ROOT)}")
    return 0


def load_sources() -> list[str]:
    """ai_snapshot.json 의 출처 목록. 환경변수가 있으면 그것만 쓴다."""
    env = (os.environ.get("AI_SNAPSHOT_SOURCE") or "").strip()
    if env:
        return [env]
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            conf = json.load(fh)
    except (OSError, ValueError):
        return []
    return [s.strip() for s in (conf.get("sources") or [])
            if isinstance(s, str) and s.strip()]


def read_source(src: str) -> bytes:
    """주소면 HTTP 로, 아니면 파일 경로로 읽는다."""
    if src.startswith(("http://", "https://")):
        headers = {"User-Agent": "gpu-grants-dashboard"}
        token = (os.environ.get("AI_SNAPSHOT_TOKEN") or "").strip()
        if token:                               # 인증이 필요한 사내 엔드포인트용
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(src, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()
    path = os.path.expanduser(src)
    if not os.path.isabs(path):
        path = os.path.join(ROOT, path)
    with open(path, "rb") as fh:
        return fh.read()


def parse(blob: bytes) -> dict:
    """형태를 확인한 뒤에만 사본을 덮어쓴다 — 반쪽짜리 파일로 화면을 깨뜨리지 않는다."""
    payload = json.loads(blob)
    if not isinstance(payload, dict):
        raise ValueError("스냅샷이 객체가 아닙니다")
    if not isinstance(payload.get("generated_at"), (int, float)):
        raise ValueError("generated_at 이 없습니다")
    if not isinstance(payload.get("summary"), dict):
        raise ValueError("summary 가 없습니다")
    return payload


def generated_at(path: str) -> float:
    """이미 가진 사본의 생성 시각(ms). 없거나 깨졌으면 0."""
    try:
        with open(path, "rb") as fh:
            value = json.load(fh).get("generated_at")
    except (OSError, ValueError):
        return 0.0
    return float(value) if isinstance(value, (int, float)) else 0.0


def when(ms: float) -> str:
    if not ms:
        return "?"
    return (datetime.fromtimestamp(ms / 1000, timezone.utc)
            .astimezone(KST).strftime("%Y-%m-%d %H:%M KST"))


def write_atomic(path: str, data: bytes) -> None:
    """웹서버가 읽는 중일 수 있으므로 옆에 쓰고 갈아끼운다(반쪽 파일 방지)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def update_meta(path: str, **fields) -> None:
    """동기화 기록. 실패해도 남긴다 — 화면이 원인을 말할 수 있어야 한다.

    synced_at(마지막 성공)은 실패한 실행이 지우지 않도록 병합한다.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            meta = json.load(fh)
        if not isinstance(meta, dict):
            meta = {}
    except (OSError, ValueError):
        meta = {}
    meta.update(fields)
    try:
        write_atomic(path, json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
    except OSError as exc:                                # noqa: BLE001
        print(f"[warn] 동기화 기록을 쓰지 못했습니다: {exc}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI 사용량 스냅샷 동기화")
    ap.add_argument("--source", action="append",
                    help="ai_snapshot.json 대신 쓸 출처(경로 또는 주소). 여러 번 줄 수 있습니다")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--meta", default=META)
    ap.add_argument("--quiet", action="store_true",
                    help="바뀐 것이 없으면 아무 말도 하지 않습니다(5분 주기 실행용)")
    ap.add_argument("--rebuild", action="store_true", help="집계 DB 를 비우고 처음부터")
    ap.add_argument("--seed", metavar="스냅샷",
                    help="기존 dashboard.json 에서 세션 누적을 이어받습니다")
    a = ap.parse_args()

    conf = load_config()
    if a.rebuild:
        try:
            os.remove(ai_store.DB_PATH)
            print("[info] 집계 DB 를 비웠습니다", file=sys.stderr)
        except OSError:
            pass
    if a.seed:
        db = ai_store.connect()
        info = ai_store.seed_from_snapshot(db, a.seed)
        print(f"[info] 기준선 반영: 세션 {info['sessions']}개, "
              f"{when(info['seed_until_ms'])} 이전은 이미 센 것으로 둡니다", file=sys.stderr)

    # inbox 가 설정돼 있으면 직접 만든다 — 남의 발행을 기다리지 않는다.
    if conf.get("inbox") and not a.source:
        return build_from_inbox(conf, a.out, a.meta, a.quiet)

    sources = a.source or load_sources()
    now_ms = int(time.time() * 1000)
    have = os.path.exists(a.out)

    if not sources:
        msg = "출처가 설정되어 있지 않습니다 (ai_snapshot.json 의 sources)"
        update_meta(a.meta, checked_at=now_ms, ok=False, source=None, error=msg)
        print(f"[info] {msg} — "
              f"{'동봉된 사본을 그대로 씁니다' if have else '사본도 없습니다'}", file=sys.stderr)
        return 0 if have else 2

    errors = []
    for src in sources:
        try:
            blob = read_source(src)
            payload = parse(blob)
        except Exception as exc:                          # noqa: BLE001
            errors.append(f"{src} → {type(exc).__name__}: {exc}")
            continue

        theirs, mine = float(payload["generated_at"]), generated_at(a.out)
        update_meta(a.meta, checked_at=now_ms, ok=True, source=src, error=None)
        if have and theirs < mine:
            # 뒤로 가지 않는다 — 출처가 옛 파일로 되돌아가도 화면을 과거로 끌고 가지 않는다
            print(f"[info] 출처가 더 오래된 스냅샷입니다 ({when(theirs)} < {when(mine)})"
                  " — 사본을 유지합니다", file=sys.stderr)
            return 0
        if have and theirs == mine:
            if not a.quiet:
                print(f"[{now_kst():%Y-%m-%d %H:%M} KST] 이미 최신 (생성 {when(theirs)}) ← {src}")
            return 0

        write_atomic(a.out, blob)
        update_meta(a.meta, synced_at=now_ms)
        print(f"[{now_kst():%Y-%m-%d %H:%M} KST] 스냅샷 {len(blob) / 1024:.0f}KB "
              f"(생성 {when(theirs)}) ← {src}")
        return 0

    reason = " / ".join(errors)
    update_meta(a.meta, checked_at=now_ms, ok=False, source=sources[0], error=reason[:500])
    print(f"[warn] 스냅샷을 받지 못했습니다 — {reason}", file=sys.stderr)
    print(f"[warn] {'기존 사본을 유지합니다' if have else '사본이 없습니다'}", file=sys.stderr)
    return 1 if have else 2


if __name__ == "__main__":
    raise SystemExit(main())
