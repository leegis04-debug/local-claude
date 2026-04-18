"""content_hash 재현성과 변조 감지."""

from __future__ import annotations

from datetime import datetime, timezone

from gstar.integrity.hash_chain import compute_content_hash, verify_node
from gstar.schema import Node


def test_content_hash_is_deterministic():
    ts = datetime(2026, 4, 18, 12, 0, tzinfo=timezone.utc)
    n = Node(id="abc", kind="fact", text="X", attrs={"k": 1}, created_at=ts)
    h1 = compute_content_hash(n)
    h2 = compute_content_hash(n)
    assert h1 == h2


def test_content_hash_changes_when_text_changes():
    ts = datetime(2026, 4, 18, tzinfo=timezone.utc)
    a = Node(id="abc", kind="fact", text="A", created_at=ts)
    b = Node(id="abc", kind="fact", text="B", created_at=ts)
    assert compute_content_hash(a) != compute_content_hash(b)


def test_content_hash_ignores_signature_and_hash_fields():
    ts = datetime(2026, 4, 18, tzinfo=timezone.utc)
    a = Node(id="abc", kind="fact", text="A", created_at=ts, content_hash="ignore-me")
    b = Node(
        id="abc",
        kind="fact",
        text="A",
        created_at=ts,
        signer_id="k1",
        signature="sig",
    )
    assert compute_content_hash(a) == compute_content_hash(b)


def test_verify_node_detects_tamper():
    ts = datetime(2026, 4, 18, tzinfo=timezone.utc)
    n = Node(id="abc", kind="fact", text="X", created_at=ts)
    n.content_hash = compute_content_hash(n)
    assert verify_node(n)

    # text 변조 후 verify 실패
    n.text = "Y"
    assert not verify_node(n)
