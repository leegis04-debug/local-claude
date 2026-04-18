"""DS218SnapshotAnchor 단위 테스트 (로컬 경로 mock)."""

from __future__ import annotations

from pathlib import Path

from gstar.integrity.anchor import DS218SnapshotAnchor


def test_anchor_write_and_verify(tmp_path: Path):
    a = DS218SnapshotAnchor(tmp_path)
    root = "a" * 64
    tx = a.anchor(root)
    assert Path(tx).exists()
    assert a.verify(root, tx) is True


def test_verify_missing_file(tmp_path: Path):
    a = DS218SnapshotAnchor(tmp_path)
    assert a.verify("x" * 64, str(tmp_path / "nope.txt")) is False


def test_verify_mismatched_root(tmp_path: Path):
    a = DS218SnapshotAnchor(tmp_path)
    tx = a.anchor("a" * 64)
    assert a.verify("b" * 64, tx) is False


def test_anchor_idempotent(tmp_path: Path):
    a = DS218SnapshotAnchor(tmp_path)
    root = "1" * 64
    p1 = a.anchor(root)
    p2 = a.anchor(root)  # 같은 root 재호출 → 같은 파일 경로
    assert p1 == p2
