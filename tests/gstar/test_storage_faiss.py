"""FAISS 인덱스 왕복 테스트."""

from __future__ import annotations

import numpy as np

from gstar.storage.faiss_index import FaissStore


def test_add_and_search(faiss_store):
    rng = np.random.default_rng(42)
    ids = [f"n{i}" for i in range(5)]
    vectors = rng.standard_normal((5, 8)).astype(np.float32)
    faiss_store.add_batch(ids, vectors)

    # 첫 벡터와 같은 벡터를 쿼리하면 자기 자신이 top-1.
    hits = faiss_store.search(vectors[0], k=3)
    assert len(hits) == 3
    assert hits[0][0] == "n0"
    assert hits[0][1] > 0.99


def test_persist_roundtrip(tmp_path):
    idx = tmp_path / "emb.faiss"
    s1 = FaissStore(idx, dim=4)
    vec = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    s1.add("only", vec)
    s1.save()

    s2 = FaissStore(idx, dim=4)
    assert len(s2) == 1
    hits = s2.search(vec, k=1)
    assert hits[0][0] == "only"


def test_empty_index_search(faiss_store):
    hits = faiss_store.search(np.zeros(8, dtype=np.float32), k=5)
    assert hits == []
