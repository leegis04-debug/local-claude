"""체인·Merkle·서명 통합 검증."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gstar.integrity.hash_chain import compute_content_hash
from gstar.integrity.merkle import merkle_root
from gstar.integrity.signing import verify_signature
from gstar.schema import Node


@dataclass
class ChainResult:
    ok: bool
    checked: int
    bad_node_ids: list[str]
    bad_reasons: list[str]


def verify_chain(nodes_in_order: list[Node]) -> ChainResult:
    """주어진 namespace 의 노드들을 created_at 순으로 검증.

    - 각 노드의 content_hash 가 재계산 값과 일치하는가
    - prev_hash 가 직전 노드 content_hash 와 일치하는가 (첫 노드는 None)
    """

    bad_ids: list[str] = []
    bad_reasons: list[str] = []
    prev = None
    for n in nodes_in_order:
        expected = compute_content_hash(n)
        if n.content_hash != expected:
            bad_ids.append(n.id)
            bad_reasons.append(f"{n.id}: content_hash mismatch")
        if prev is None:
            if n.prev_hash is not None:
                bad_ids.append(n.id)
                bad_reasons.append(f"{n.id}: first node must have prev_hash=None")
        else:
            if n.prev_hash != prev.content_hash:
                bad_ids.append(n.id)
                bad_reasons.append(
                    f"{n.id}: prev_hash does not link to {prev.id}"
                )
        prev = n

    return ChainResult(
        ok=not bad_ids,
        checked=len(nodes_in_order),
        bad_node_ids=bad_ids,
        bad_reasons=bad_reasons,
    )


def verify_cluster_merkle(member_hashes: list[str], stored_root: str | None) -> bool:
    if stored_root is None:
        return False
    return merkle_root(member_hashes) == stored_root


def verify_node_signature(gstar_home: Path, node: Node) -> bool:
    """서명 있으면 검증, 없으면 True (미래 호환성)."""
    if not node.signature:
        return True
    return verify_signature(gstar_home, node.source_namespace, node.content_hash, node.signature)
