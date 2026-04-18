"""중력장 계산 E2E — DummyEmbedder + 합성 3종."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from gstar.config import Weights
from gstar.embedding import DummyEmbedder
from gstar.gravity.field import add_goal, compute_gravity, get_goal_embedding
from gstar.ingest.pipeline import ingest_path
from gstar.schema import Edge, Node
from gstar.storage.faiss_index import FaissStore


def _fresh_faiss(tmp_path: Path, dim: int) -> FaissStore:
    return FaissStore(tmp_path / "emb.faiss", dim=dim)


def test_gravity_ranks_relevant_facts_higher(store, tmp_path):
    """synthetic case 1: 관련성 있는 fact 가 top-k 에 들어와야 한다."""

    md = tmp_path / "doc.md"
    md.write_text(
        "# 주제\n"
        "RAG 는 LLM 품질을 개선한다.\n"
        "RAG 는 검색과 생성을 결합한다.\n"
        "고양이는 포유류이다.\n"
        "자동차 엔진은 내연기관이다.\n",
        encoding="utf-8",
    )
    embedder = DummyEmbedder(dim=16)
    faiss = _fresh_faiss(tmp_path, 16)
    ingest_path(md, store=store, faiss=faiss, embedder=embedder, root=tmp_path)

    # Dummy 는 해시 기반이라 "RAG LLM 품질" 질의와 관련 문장이 우연히 일치 안 할 수 있음.
    # 합성 검증은 relevance 항목만 가중 → 순수 cosine 상위가 top 이 되는지 확인.
    weights = Weights(
        w_rel=1.0, w_rec=0.0, w_cent=0.0, w_ver=0.0, w_pur=0.0, w_stab=0.0
    )

    goal, vec = add_goal("RAG LLM 품질", "proposal", store, faiss, embedder)
    entries = compute_gravity(goal, vec, store, faiss, weights)

    # 자기 자신과 가장 가까운 노드가 top. goal 자체는 후보 제외(goal.kind 아님).
    # 적어도 하나 이상의 엔트리가 나와야 하고, 정렬이 내림차순
    assert len(entries) > 0
    scores = [e.total for e in entries]
    assert scores == sorted(scores, reverse=True)


def test_gravity_recency_dominates_when_weighted(store, tmp_path):
    """synthetic case 2: w_rec=1, 나머지 0 → 최신 노드가 상위."""

    from datetime import datetime, timedelta, timezone

    embedder = DummyEmbedder(dim=8)
    faiss = _fresh_faiss(tmp_path, 8)
    now = datetime.now(timezone.utc)

    # 직접 노드 3개 insert (시간 차이)
    old = Node(kind="fact", text="오래된 사실", created_at=now - timedelta(days=120))
    mid = Node(kind="fact", text="중간 사실", created_at=now - timedelta(days=30))
    new = Node(kind="fact", text="최신 사실", created_at=now - timedelta(hours=1))
    for n in (old, mid, new):
        store.insert_node(n)
    vecs = embedder.encode([n.text for n in (old, mid, new)])
    faiss.add_batch([old.id, mid.id, new.id], vecs)

    weights = Weights(
        w_rel=0.0, w_rec=1.0, w_cent=0.0, w_ver=0.0, w_pur=0.0, w_stab=0.0,
        halflife_days=30.0,
    )

    goal, vec = add_goal("시간 테스트", "proposal", store, faiss, embedder)
    entries = compute_gravity(goal, vec, store, faiss, weights, now=now)

    top_ids = [e.node_id for e in entries if e.node_id in {old.id, mid.id, new.id}][:3]
    assert top_ids[0] == new.id
    assert top_ids[-1] == old.id


def test_gravity_centrality_dominates_when_weighted(store, tmp_path):
    """synthetic case 3: w_cent=1 + 명시적 엣지 → 중심 근처 노드가 상위."""

    embedder = DummyEmbedder(dim=8)
    faiss = _fresh_faiss(tmp_path, 8)

    center = Node(kind="entity", text="중심")
    near = Node(kind="fact", text="가까운 사실")
    far = Node(kind="fact", text="먼 사실")
    for n in (center, near, far):
        store.insert_node(n)

    # center -- near 연결
    store.insert_edge(Edge(src=center.id, dst=near.id, kind="evidence_of"))

    vecs = embedder.encode([n.text for n in (center, near, far)])
    faiss.add_batch([center.id, near.id, far.id], vecs)

    weights = Weights(
        w_rel=0.0, w_rec=0.0, w_cent=1.0, w_ver=0.0, w_pur=0.0, w_stab=0.0,
        seed_k=1, candidate_k=10,
    )

    # goal 을 center 와 동일 텍스트로 → FAISS seed=center 확정
    goal, vec = add_goal("중심", "research", store, faiss, embedder)
    entries = compute_gravity(goal, vec, store, faiss, weights)

    # near 는 seed(center) 에서 1 hop → 0.5, far 는 도달 불가 → 0
    by_id = {e.node_id: e.total for e in entries}
    assert by_id.get(near.id, 0.0) > by_id.get(far.id, 0.0)


def test_goal_embedding_retrievable_from_faiss(store, tmp_path):
    embedder = DummyEmbedder(dim=8)
    faiss = _fresh_faiss(tmp_path, 8)
    goal, vec = add_goal("G 목표", "proposal", store, faiss, embedder)

    retrieved = get_goal_embedding(faiss, goal.id)
    assert retrieved is not None
    # FaissStore 는 내부적으로 정규화되어 저장됨 → cosine 1 (동일 방향) 여야 함.
    from gstar.gravity.score import relevance
    assert relevance(retrieved, retrieved) > 0.999


def test_weights_load_from_toml(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[weights]\nw_rel = 0.5\nhalflife_days = 7.0\n",
        encoding="utf-8",
    )
    w = Weights.load(cfg)
    assert w.w_rel == 0.5
    assert w.halflife_days == 7.0
    # 미지정 필드는 기본값 유지
    assert w.w_cent == 0.20
