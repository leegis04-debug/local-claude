"""Merkle 루트 계산.

Cluster(지식 항성) 의 멤버 content_hash 들을 정렬 후 binary tree 해시.
멤버 집합이 같다면 입력 순서 무관하게 같은 root 가 나오도록 정렬 강제.
"""

from __future__ import annotations

import hashlib


EMPTY_HASH = hashlib.sha256(b"").hexdigest()


def merkle_root(leaves: list[str]) -> str:
    """정렬된 leaf 해시들에서 Merkle root 계산. 빈 집합은 EMPTY_HASH."""

    if not leaves:
        return EMPTY_HASH

    level = sorted(leaves)
    while len(level) > 1:
        next_level: list[str] = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left  # 홀수면 self-pair
            combined = hashlib.sha256((left + right).encode("utf-8")).hexdigest()
            next_level.append(combined)
        level = next_level
    return level[0]


def verify_merkle(leaves: list[str], root: str) -> bool:
    return merkle_root(leaves) == root
