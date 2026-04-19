"""Qdrant 전체 collection payload dump — Phase A2 용.

미니 PC 에서 실행. Gateway 컨테이너 네트워크 경유 또는 Qdrant 직접(6333) 접근.

출력: ~/gstar-data/qdrant_meta/<collection>.jsonl
- 각 라인: {id, path?, source?, project?, tags?, date?, security_level?, priority?, axis?, doc_type?, ...}
- vector 는 제외 (dim=1024 이지만 G 는 자체 SBERT 재임베딩 사용)
- text_preview 는 유지 (검증·디버깅용, ingest 시 참고)

사용:
    python3 scripts/qdrant_dump.py --qdrant-host http://localhost:6333 --out ~/gstar-data/qdrant_meta/
    # 또는 docker exec 경유
    docker exec assistant-gateway python /path/to/qdrant_dump.py --qdrant-host http://assistant-qdrant:6333 --out /app/state/qdrant_meta
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


def _post(url: str, body: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url: str, timeout: int = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def list_collections(qdrant: str) -> list[str]:
    d = _get(f"{qdrant}/collections")
    return [c["name"] for c in d["result"]["collections"]]


def count_collection(qdrant: str, col: str) -> int:
    try:
        d = _post(f"{qdrant}/collections/{col}/points/count", {})
        return int(d["result"]["count"])
    except Exception:
        return -1


def dump_collection(qdrant: str, col: str, out_path: Path, batch: int = 512) -> int:
    """collection 의 모든 point payload 를 jsonl 로 저장. vector 제외."""
    offset = None
    total = 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        while True:
            body: dict = {
                "limit": batch,
                "with_payload": True,
                "with_vector": False,
            }
            if offset is not None:
                body["offset"] = offset
            try:
                d = _post(f"{qdrant}/collections/{col}/points/scroll", body)
            except urllib.error.URLError as e:
                print(f"  [{col}] ERR {e}", file=sys.stderr)
                break
            pts = d["result"].get("points", [])
            for p in pts:
                row = {"id": p.get("id"), "collection": col}
                payload = p.get("payload") or {}
                for k, v in payload.items():
                    row[k] = v
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                total += 1
            offset = d["result"].get("next_page_offset")
            if offset is None or not pts:
                break
    return total


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--qdrant-host", default=os.environ.get("QDRANT_HOST", "http://localhost:6333"),
                   help="Qdrant REST URL")
    p.add_argument("--out", default=os.environ.get("QDRANT_DUMP_DIR", "./qdrant_meta"),
                   help="출력 디렉터리")
    p.add_argument("--collections", default=None,
                   help="쉼표 구분 collection 명. 미지정 시 전체")
    p.add_argument("--batch", type=int, default=512)
    args = p.parse_args()

    out_dir = Path(args.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.collections:
        cols = [c.strip() for c in args.collections.split(",") if c.strip()]
    else:
        cols = list_collections(args.qdrant_host)

    summary = {}
    t0 = time.time()
    for col in cols:
        cnt = count_collection(args.qdrant_host, col)
        if cnt == 0:
            print(f"  {col}: 0 (skip)")
            continue
        if cnt < 0:
            print(f"  {col}: count 실패 (skip)")
            continue
        out_path = out_dir / f"{col}.jsonl"
        t1 = time.time()
        written = dump_collection(args.qdrant_host, col, out_path, batch=args.batch)
        dt = time.time() - t1
        summary[col] = {"expected": cnt, "written": written, "sec": round(dt, 1)}
        print(f"  {col}: {written}/{cnt} ({dt:.1f}s) → {out_path}")

    (out_dir / "_summary.json").write_text(
        json.dumps(
            {"cols": summary, "total_sec": round(time.time() - t0, 1), "qdrant": args.qdrant_host},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nDONE. total={sum(s['written'] for s in summary.values())}  elapsed={time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
