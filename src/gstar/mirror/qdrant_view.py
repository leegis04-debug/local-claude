"""Phase H5 — G fact/evidence/section → Qdrant 파생 뷰 emitter.

원칙: G 가 중간표현(canonical). Qdrant 는 검색 뷰.
각 Qdrant point 의 payload 에 `g_node_id` 를 남겨 provenance 추적.

collection: `g_mirror` (dim=384, cosine). 기존 Qdrant collection 과 분리.
derived_view 테이블: (g_node_id, "qdrant", point_id, "g_mirror", emitted_at, ok/failed).

env:
  GATEWAY_URL            (Gateway/Qdrant REST 경유 URL)
  QDRANT_MIRROR_URL      (기본: http://100.79.251.53:6333 — Meshnet 직통)
  QDRANT_MIRROR_COLL     (기본: g_mirror)
  QDRANT_MIRROR_KINDS    (기본: "fact,evidence,section" — 쉼표 구분)
  QDRANT_MIRROR_BATCH    (기본 128)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class MirrorResult:
    collection: str
    scanned: int = 0
    upserted: int = 0
    skipped_existing: int = 0
    failed: int = 0
    errors: list[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _qdrant_url() -> str:
    return os.environ.get("QDRANT_MIRROR_URL", "http://100.79.251.53:6333")


def _collection_name() -> str:
    return os.environ.get("QDRANT_MIRROR_COLL", "g_mirror")


def _target_kinds() -> set[str]:
    raw = os.environ.get("QDRANT_MIRROR_KINDS", "fact,evidence,section")
    return {k.strip() for k in raw.split(",") if k.strip()}


def _get(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _put(url: str, body: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _post(url: str, body: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ensure_collection(dim: int, coll: str | None = None) -> None:
    """collection 존재 보장. 없으면 cosine + dim 으로 생성."""
    coll = coll or _collection_name()
    base = _qdrant_url()
    try:
        _get(f"{base}/collections/{coll}")
        return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    # 생성
    body = {
        "vectors": {"size": dim, "distance": "Cosine"},
        "on_disk_payload": True,
    }
    _put(f"{base}/collections/{coll}", body)


def _already_mirrored(store, g_node_id: str, coll: str) -> bool:
    with store.lock:
        row = store.conn.execute(
            "SELECT 1 FROM derived_view WHERE g_node_id=? AND view='qdrant' "
            "AND collection=? AND status='ok' LIMIT 1",
            [g_node_id, coll],
        ).fetchone()
    return row is not None


def _mark_mirrored(
    store, g_node_id: str, point_id: str, coll: str, status: str = "ok",
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    with store.lock:
        store.conn.execute(
            "INSERT OR REPLACE INTO derived_view "
            "(g_node_id, view, external_id, collection, emitted_at, status, error) "
            "VALUES (?, 'qdrant', ?, ?, ?, ?, ?)",
            [g_node_id, point_id, coll, now, status, error],
        )


def _fetch_pending(store, kinds: set[str], coll: str, limit: int) -> list[dict]:
    """mirror 대상 노드 후보 (fact/evidence/section 중, derived_view 에 없는 것)."""
    placeholders = ",".join(["?"] * len(kinds))
    with store.lock:
        rows = store.conn.execute(
            f"SELECT n.id, n.kind, n.text, n.source_namespace, n.attrs_json, n.created_at "
            f"FROM node n "
            f"WHERE n.kind IN ({placeholders}) AND NOT EXISTS ("
            "    SELECT 1 FROM derived_view d WHERE d.g_node_id = n.id "
            "      AND d.view='qdrant' AND d.collection=? AND d.status='ok'"
            ") ORDER BY n.created_at DESC LIMIT ?",
            [*kinds, coll, limit],
        ).fetchall()
    out = []
    for r in rows:
        try:
            attrs = json.loads(r[4]) if r[4] else {}
        except Exception:
            attrs = {}
        out.append(
            {
                "id": r[0],
                "kind": r[1],
                "text": r[2] or "",
                "ns": r[3],
                "attrs": attrs,
                "created_at": r[5],
            }
        )
    return out


def mirror_to_qdrant(
    store,
    embedder,
    *,
    limit: int = 1000,
    batch_size: int | None = None,
    coll: str | None = None,
) -> MirrorResult:
    """store 의 미러 안 된 fact/evidence/section 을 Qdrant 에 upsert.

    원칙: 각 point id = g_node_id (UUID-ish ULID). payload 에 full g context.
    """
    coll = coll or _collection_name()
    kinds = _target_kinds()
    batch = batch_size or int(os.environ.get("QDRANT_MIRROR_BATCH", "128"))
    base = _qdrant_url()
    res = MirrorResult(collection=coll)

    # collection 생성 (dim = embedder.dim)
    try:
        ensure_collection(embedder.dim, coll=coll)
    except Exception as exc:
        res.errors.append(f"ensure_collection failed: {exc}")
        return res

    pending = _fetch_pending(store, kinds, coll, limit)
    res.scanned = len(pending)
    if not pending:
        return res

    # 배치 임베딩
    for i in range(0, len(pending), batch):
        chunk = pending[i : i + batch]
        texts = [c["text"] for c in chunk]
        try:
            vecs = embedder.encode(texts)
        except Exception as exc:
            res.failed += len(chunk)
            res.errors.append(f"encode failed batch {i}: {exc}")
            continue

        points = []
        for c, vec in zip(chunk, vecs):
            payload = {
                "g_node_id": c["id"],
                "g_kind": c["kind"],
                "g_namespace": c["ns"],
                "text_preview": c["text"][:512],
                "source": (c["attrs"] or {}).get("source"),
                "section": (c["attrs"] or {}).get("section"),
                "tags": (c["attrs"] or {}).get("tags") or [],
            }
            points.append(
                {
                    "id": _point_id_for(c["id"]),
                    "vector": vec.tolist() if hasattr(vec, "tolist") else list(vec),
                    "payload": payload,
                }
            )

        try:
            _put(f"{base}/collections/{coll}/points?wait=true", {"points": points})
            for c in chunk:
                _mark_mirrored(store, c["id"], _point_id_for(c["id"]), coll)
            res.upserted += len(chunk)
        except Exception as exc:
            res.failed += len(chunk)
            res.errors.append(f"upsert failed batch {i}: {exc}")
            for c in chunk:
                _mark_mirrored(store, c["id"], _point_id_for(c["id"]), coll,
                               status="failed", error=str(exc)[:200])

    return res


def _point_id_for(g_node_id: str) -> str:
    """Qdrant point id. ULID 는 Qdrant unnamed integer/UUID 를 요구 → hex digest 의 숫자 형태.

    간단하고 안정적 매핑: sha256 첫 16바이트 → int → 16진 UUID 형식.
    """
    import hashlib
    h = hashlib.sha256(g_node_id.encode("utf-8")).hexdigest()
    # UUIDv4 형식: 8-4-4-4-12
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
