"""Phase H9 — legacy Qdrant/Neo4j ↔ G anchor 브리지.

원칙 (사용자 정리 2026-04-19):
- legacy 데이터가 고가치이므로 전량 매칭 시도
- 전량 시도 ≠ 전량 강제 정합화
- 신뢰도 3등급: high(자동) / medium(검토) / low(legacy_only)
- legacy 원본은 덮어쓰지 않고 "연결 레이어만 추가"

흐름 (G → legacy 방향):
1. G 의 fact/entity 노드 순회 (derived_view 에 'legacy_qdrant' 없는 것)
2. 각 G 노드 text 를 legacy Qdrant `/search/hybrid` 로 질의 (상위 proposals/code_repos/...)
3. top-1 의 score·cosine 으로 3등급 판정:
     HIGH     : score >= 0.85 (자동 연결)
     MEDIUM   : 0.70 <= score < 0.85 (검토 후 연결)
     LOW      : score <  0.70 (legacy_only)
4. derived_view 에 (g_node_id, view='legacy_qdrant', external_id=qdrant_point_id,
                    status=legacy_high|legacy_medium|legacy_low) 기록
5. Neo4j 쪽은 간단한 이름 매칭 (Neo4j Paper.title / Method.name 과 G entity.text)

env:
    LEGACY_BRIDGE_ENABLED  (기본 on)
    LEGACY_BRIDGE_LIMIT    (tick 당 처리 수, 기본 500)
    LEGACY_HIGH_THRESHOLD  (기본 0.85)
    LEGACY_MEDIUM_THRESHOLD (기본 0.70)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class LegacyBridgeResult:
    scanned: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    errors: int = 0


def _gateway_url() -> str:
    return os.environ.get("GATEWAY_URL", "http://100.79.251.53:8000")


def _token() -> str:
    return os.environ.get("ASST_TOKEN", "")


def _legacy_search(query: str, top_k: int = 3, timeout: int = 5) -> list[dict]:
    """Gateway /search/hybrid — legacy Qdrant+Neo4j 연동 경로 (G mirror 이전 데이터)."""
    body = json.dumps({"query": query[:500], "top_k": top_k}).encode("utf-8")
    req = urllib.request.Request(
        f"{_gateway_url().rstrip('/')}/search/hybrid",
        data=body,
        headers={"Content-Type": "application/json", "X-Auth-Token": _token()},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    return d.get("results") or d.get("hits") or []


def _fetch_unlinked(store, limit: int, kinds: tuple[str, ...] = ("fact", "entity")) -> list[dict]:
    placeholders = ",".join(["?"] * len(kinds))
    with store.lock:
        rows = store.conn.execute(
            f"SELECT n.id, n.kind, n.text, n.source_namespace "
            f"FROM node n WHERE n.kind IN ({placeholders}) "
            f"AND n.source_namespace <> 'graph_import' "   # A3 import 는 skip
            f"AND n.source_namespace <> 'claude_traces' "
            f"AND NOT EXISTS ("
            "  SELECT 1 FROM derived_view d "
            "  WHERE d.g_node_id = n.id AND d.view = 'legacy_qdrant'"
            ") "
            "ORDER BY n.created_at DESC LIMIT ?",
            [*kinds, limit],
        ).fetchall()
    return [{"id": r[0], "kind": r[1], "text": r[2] or "", "ns": r[3]} for r in rows]


def _record_link(
    store, g_node_id: str, external_id: str, status: str,
    collection: str | None = None, score: float = 0.0,
) -> None:
    now = datetime.now(timezone.utc)
    with store.lock:
        store.conn.execute(
            "INSERT OR REPLACE INTO derived_view "
            "(g_node_id, view, external_id, collection, emitted_at, status, error) "
            "VALUES (?, 'legacy_qdrant', ?, ?, ?, ?, ?)",
            [g_node_id, external_id, collection, now, status, f"score={score:.3f}"],
        )


def bridge_g_to_legacy(store, *, limit: int | None = None) -> LegacyBridgeResult:
    res = LegacyBridgeResult()
    if not _token():
        res.errors += 1
        return res

    limit = limit or int(os.environ.get("LEGACY_BRIDGE_LIMIT", "500"))
    high_t = float(os.environ.get("LEGACY_HIGH_THRESHOLD", "0.85"))
    med_t = float(os.environ.get("LEGACY_MEDIUM_THRESHOLD", "0.70"))

    candidates = _fetch_unlinked(store, limit)
    res.scanned = len(candidates)

    for c in candidates:
        if not c["text"].strip() or len(c["text"]) < 4:
            _record_link(store, c["id"], "", "legacy_low", score=0.0)
            res.low += 1
            continue
        hits = _legacy_search(c["text"], top_k=1, timeout=5)
        if not hits:
            _record_link(store, c["id"], "", "legacy_low", score=0.0)
            res.low += 1
            continue
        top = hits[0]
        score = float(top.get("score") or top.get("sim") or 0.0)
        src_raw = (top.get("source") or "").lower()
        # Gateway g_mirror 자체는 우리 파생이므로 제외 — collection 감지
        if "g_mirror" in src_raw:
            _record_link(store, c["id"], "", "legacy_low", score=0.0)
            res.low += 1
            continue

        ext_id = str(top.get("id") or top.get("node_id") or "")
        coll = str(top.get("collection") or top.get("source") or "")

        if score >= high_t:
            status = "legacy_high"
            res.high += 1
        elif score >= med_t:
            status = "legacy_medium"
            res.medium += 1
        else:
            status = "legacy_low"
            res.low += 1
        _record_link(store, c["id"], ext_id, status, collection=coll, score=score)

    return res
