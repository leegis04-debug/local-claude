"""Worker CLI — `python -m gstar.worker.cli` 로 상주 실행.

env:
  WORKER_INTERVAL_SEC (기본 900)     — tick 간격. 0 이면 단발 실행 후 종료.
  WORKER_ENABLED      (기본 on)      — off 면 loop 진입 전 종료
  GSTAR_HOME                          — DuckDB + FAISS 상태 디렉터리
  GP_COMMUNITY_MIN_SIZE (기본 3)     — Louvain 최소 커뮤니티 크기

사용 (컨테이너 상주):
  CMD ["python", "-m", "gstar.worker.cli"]
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def _get_store():
    """DuckStore 단일 인스턴스 생성."""
    from gstar.storage.duckdb_store import DuckStore

    home = Path(os.environ.get("GSTAR_HOME", str(Path.home() / ".gstar")))
    db = home / "state" / "g.duckdb"
    return DuckStore(db)


def _once(store, min_community_size: int) -> dict:
    from gstar.worker.cycle import run_cycle

    rep = run_cycle(store, min_community_size=min_community_size)
    return rep.to_json()


def main() -> int:
    if os.environ.get("WORKER_ENABLED", "on").lower() in {"off", "0", "false"}:
        print("WORKER_ENABLED=off → exit", flush=True)
        return 0

    interval = int(os.environ.get("WORKER_INTERVAL_SEC", "900"))
    min_cs = int(os.environ.get("GP_COMMUNITY_MIN_SIZE", "3"))

    store = _get_store()
    print(f"[worker] started. interval={interval}s, min_community_size={min_cs}", flush=True)

    if interval <= 0:
        rep = _once(store, min_cs)
        print(json.dumps(rep, ensure_ascii=False, indent=2), flush=True)
        return 0

    # 상주 루프
    while True:
        try:
            rep = _once(store, min_cs)
            print(json.dumps(rep, ensure_ascii=False), flush=True)
        except KeyboardInterrupt:
            print("[worker] interrupted", flush=True)
            return 0
        except Exception as exc:
            print(f"[worker] tick failed: {type(exc).__name__}: {exc}", flush=True, file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
