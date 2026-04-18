"""append-only 해시 체인.

content_hash = sha256(canonical_json(core_fields))
chain_link(node) = sha256(content_hash || prev_hash)

검증 시 (1) 각 노드의 content_hash 재계산 일치, (2) prev_hash 가 동일 namespace 의
직전 노드 content_hash 와 일치하는지 확인.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from gstar.schema import Node


def canonical_json(obj: Any) -> str:
    """정규화된 JSON 직렬화. key 정렬, 공백 제거, utf-8 그대로."""
    return json.dumps(
        obj,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def _normalize_ts(ts: datetime) -> str:
    """datetime 을 UTC 초단위 ISO 로 정규화. naive 는 UTC 로 가정.

    DuckDB 가 TIMESTAMP 컬럼을 naive 로 돌려주기 때문에 insert/fetch 간 표현 차이가
    content_hash 를 깨뜨리지 않도록 이 함수를 공통으로 사용한다.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _json_default(x: Any) -> Any:
    if isinstance(x, datetime):
        return _normalize_ts(x)
    if hasattr(x, "isoformat"):
        return x.isoformat()
    raise TypeError(f"cannot serialize {type(x)}")


def compute_content_hash(node: Node) -> str:
    """node 의 불변 필드들에 대한 sha256. content_hash, prev_hash, signer_id,
    signature 자신은 제외(순환 방지)."""

    payload = {
        "id": node.id,
        "kind": node.kind,
        "text": node.text,
        "attrs": node.attrs,
        "created_at": _normalize_ts(node.created_at),
        "version": node.version,
        "prev_version_id": node.prev_version_id,
        "source_namespace": node.source_namespace,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def verify_node(node: Node) -> bool:
    """단일 노드의 content_hash 필드가 재계산 값과 일치하는지."""
    expected = compute_content_hash(node)
    return node.content_hash == expected
