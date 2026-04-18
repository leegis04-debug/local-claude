"""Merkle root 결정성·독립성."""

from __future__ import annotations

from gstar.integrity.merkle import EMPTY_HASH, merkle_root, verify_merkle


def test_empty_set():
    assert merkle_root([]) == EMPTY_HASH


def test_single_leaf():
    h = "a" * 64
    root = merkle_root([h])
    # 단일 leaf 는 leaf 자체이거나 결정적 변환 — 둘 다 허용, 재현성만 확인.
    assert root == merkle_root([h])


def test_order_independence():
    a, b, c = "a" * 64, "b" * 64, "c" * 64
    assert merkle_root([a, b, c]) == merkle_root([c, a, b])
    assert merkle_root([a, b, c]) == merkle_root([b, c, a])


def test_different_sets_different_roots():
    a, b, c, d = [ch * 64 for ch in "abcd"]
    assert merkle_root([a, b, c]) != merkle_root([a, b, d])


def test_verify_merkle_helper():
    leaves = ["a" * 64, "b" * 64, "c" * 64]
    root = merkle_root(leaves)
    assert verify_merkle(leaves, root)
    assert not verify_merkle(leaves, "0" * 64)
