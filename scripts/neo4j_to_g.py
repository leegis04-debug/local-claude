"""Neo4j → G dump 전용 스크립트 (Phase A3).

Gateway `/graph/query` 로 Neo4j 전체 노드·관계를 읽어 jsonl 2개로 저장:
    <out>/nodes.jsonl  {nid, label, props}
    <out>/edges.jsonl  {src, dst, rel, props}

apply 는 g-serve 서버의 POST /ingest/neo4j 엔드포인트 사용 (단일 writer).

사용:
    ASST_TOKEN=... python scripts/neo4j_to_g.py dump \\
        --gateway http://localhost:8000 \\
        --out ~/gstar-data/neo4j_dump

    # 이후 (미니 PC): 같은 경로를 /ingest/neo4j 에 전달
    curl -X POST http://localhost:9999/ingest/neo4j \\
        -d '{"dump_dir":"/app/state/neo4j_dump","namespace":"graph_import"}'
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path


GATEWAY_DEFAULT = "http://localhost:8000"


def _post_cypher(gateway: str, token: str, cypher: str, timeout: int = 60) -> list[dict]:
    body = json.dumps({"cypher": cypher}).encode("utf-8")
    req = urllib.request.Request(
        f"{gateway.rstrip('/')}/graph/query",
        data=body,
        headers={"Content-Type": "application/json", "X-Auth-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}") from e
    return d.get("results") or []


def dump_nodes(gateway: str, token: str, out_path: Path) -> int:
    """모든 노드 덤프 — embedding 속성 제외."""
    cypher = (
        "MATCH (n) "
        "WITH n, labels(n)[0] AS label, "
        "  [k IN keys(n) WHERE k<>'embedding'] AS ks "
        "RETURN id(n) AS nid, label, "
        "  [k IN ks | [k, n[k]]] AS props_kv"
    )
    rows = _post_cypher(gateway, token, cypher, timeout=300)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            props = {k: v for k, v in (r.get("props_kv") or [])}
            rec = {"nid": r["nid"], "label": r.get("label"), "props": props}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(rows)


def dump_edges(gateway: str, token: str, out_path: Path) -> int:
    cypher = (
        "MATCH (a)-[r]->(b) "
        "RETURN id(a) AS src, id(b) AS dst, type(r) AS rel, "
        "  [k IN keys(r) | [k, r[k]]] AS props_kv"
    )
    rows = _post_cypher(gateway, token, cypher, timeout=300)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in rows:
            props = {k: v for k, v in (r.get("props_kv") or [])}
            rec = {"src": r["src"], "dst": r["dst"], "rel": r.get("rel"), "props": props}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(rows)


def dump_all(gateway: str, token: str, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = dump_nodes(gateway, token, out_dir / "nodes.jsonl")
    e = dump_edges(gateway, token, out_dir / "edges.jsonl")
    summary = {"nodes": n, "edges": e, "gateway": gateway, "out": str(out_dir)}
    (out_dir / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    pd = sub.add_parser("dump", help="Neo4j → jsonl dump via Gateway")
    pd.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", GATEWAY_DEFAULT))
    pd.add_argument("--token", default=os.environ.get("ASST_TOKEN", ""))
    pd.add_argument("--out", default="./neo4j_dump")

    args = p.parse_args()

    if args.cmd == "dump":
        if not args.token:
            print("ERROR: ASST_TOKEN env 또는 --token 필요", file=sys.stderr)
            return 2
        out_dir = Path(args.out).expanduser()
        summary = dump_all(args.gateway, args.token, out_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
