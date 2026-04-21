"""Phase E3.5 — Reinforce 루프 (G 우선, Gateway 폴백).

Verifier L2 를 통과해 `trust_score >= threshold` 에 도달한 fact 를 G `/notes` 로
역축적 (G 가 Gateway mirror 까지 자동 proxy). G 도달 실패 시에만 Gateway `/notes`
직결로 폴백.

source 는 `g:<node_id>` 로 태깅 → 순환 검증 방지 (L2 대조 시 자기 자신 제외 가능).
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass
class ReinforceResult:
    scanned: int = 0
    eligible: int = 0
    pushed: int = 0
    failed: int = 0
    errors: list[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _fetch_eligible_facts(
    store,
    *,
    min_trust: float,
    namespace: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """trust_score >= min_trust 이고 아직 reinforce 로그에 없는 fact 리스트."""
    with store.lock:
        params: list = [min_trust]
        sql = (
            "SELECT id, text, trust_score, source_namespace, attrs_json, created_at "
            "FROM node WHERE kind='fact' AND trust_score >= ? "
        )
        if namespace:
            sql += "AND source_namespace = ? "
            params.append(namespace)
        sql += "ORDER BY trust_score DESC, last_verified_at DESC LIMIT ?"
        params.append(limit)
        rows = store.conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        try:
            attrs = json.loads(r[4]) if r[4] else {}
        except Exception:
            attrs = {}
        out.append(
            {
                "node_id": r[0],
                "text": r[1],
                "trust_score": float(r[2] or 0.0),
                "namespace": r[3],
                "attrs": attrs,
                "created_at": r[5],
            }
        )
    return out


def _push_g_note(
    g_url: str,
    *,
    text: str,
    tags: list[str],
    source: str,
    namespace: str = "personal_notes",
    timeout: float = 5.0,
) -> bool:
    """G `/notes` — 상위 계층. 인증 불필요. GP_MIRROR_QDRANT=on 이면 Gateway mirror 자동."""
    body = json.dumps(
        {"text": text, "source": source, "tags": tags, "namespace": namespace},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{g_url.rstrip('/')}/notes",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return True
    except Exception:
        return False


def _push_gateway_note(
    gateway_url: str,
    token: str,
    *,
    text: str,
    tags: list[str],
    source: str,
    timeout: float = 5.0,
) -> bool:
    """Gateway `/notes` 직결 — G 도달 실패 시에만 fallback."""
    body = json.dumps(
        {"text": text, "source": source, "tags": tags}, ensure_ascii=False
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/notes",
        data=body,
        headers={"Content-Type": "application/json", "X-Auth-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
        return True
    except Exception:
        return False


def reinforce_to_gateway(
    store,
    *,
    min_trust: float = 3.0,
    namespace: str | None = None,
    gateway_url: str | None = None,
    g_url: str | None = None,
    token: str | None = None,
    limit: int = 200,
    timeout: float = 5.0,
) -> ReinforceResult:
    """trust≥min_trust fact 를 G `/notes` 로 우선 역축적 (Gateway mirror 자동).

    G 도달 실패 시에만 Gateway `/notes` 직결로 폴백.
    멱등성: 각 fact 의 attrs_json 에 `reinforced_at` 기록. 이후 호출 시 skip.
    """
    gurl = gateway_url or os.environ.get("GATEWAY_URL", "http://100.79.251.53:8000")
    g_url = g_url or os.environ.get("G_URL", "http://100.79.251.53:9999")
    tok = token or os.environ.get("ASST_TOKEN", "")
    res = ReinforceResult()

    candidates = _fetch_eligible_facts(
        store, min_trust=min_trust, namespace=namespace, limit=limit
    )
    res.scanned = len(candidates)

    now = datetime.now(timezone.utc)
    for f in candidates:
        if (f["attrs"] or {}).get("reinforced_at"):
            continue
        res.eligible += 1
        nid = f["node_id"]
        tags = [
            "g_verified",
            f"trust:{f['trust_score']:.1f}",
            f"ns:{f['namespace']}",
        ]
        # G primary — 인증 불필요
        ok = _push_g_note(
            g_url,
            text=f["text"],
            tags=tags,
            source=f"g:{nid}",
            timeout=timeout,
        )
        # Gateway fallback — G 실패 + 토큰 있을 때만
        if not ok and tok:
            ok = _push_gateway_note(
                gurl,
                tok,
                text=f["text"],
                tags=tags,
                source=f"g:{nid}",
                timeout=timeout,
            )
        if ok:
            res.pushed += 1
            # attrs 에 reinforced_at 기록 (멱등성)
            f["attrs"]["reinforced_at"] = now.isoformat()
            with store.lock:
                try:
                    store.conn.execute(
                        "UPDATE node SET attrs_json=? WHERE id=?",
                        [json.dumps(f["attrs"], ensure_ascii=False), nid],
                    )
                except Exception as exc:
                    res.errors.append(f"attrs update fail {nid}: {exc}")
        else:
            res.failed += 1
    return res
