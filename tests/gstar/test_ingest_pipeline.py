"""Ingest 파이프라인 E2E (DummyEmbedder 주입)."""

from __future__ import annotations

from pathlib import Path

from gstar.embedding import DummyEmbedder
from gstar.ingest.pipeline import ingest_path
from gstar.storage.faiss_index import FaissStore


def test_ingest_creates_nodes_and_edges(store, tmp_path: Path):
    md = tmp_path / "proposal.md"
    md.write_text(
        "# 배경\n"
        "이재원은 다겸의 책임연구원이다.\n"
        "이재원은 OptiREC 를 개발했다.\n"
        "## 기술\n"
        "OptiREC 는 RAG 와 결합된다.\n"
        "RAG 는 LLM 품질을 높인다.\n",
        encoding="utf-8",
    )
    embedder = DummyEmbedder(dim=16)
    faiss = FaissStore(tmp_path / "emb.faiss", dim=embedder.dim)

    report = ingest_path(md, store=store, faiss=faiss, embedder=embedder, root=tmp_path)

    assert report.files_scanned == 1
    assert report.facts >= 4

    # fact / entity 노드 분류
    facts = store.list_nodes(kind="fact")
    ents = store.list_nodes(kind="entity")
    assert len(facts) == report.facts
    assert len(ents) == report.entities

    ent_names = {e.text for e in ents}
    assert "이재원" in ent_names
    assert "OptiREC" in ent_names
    assert "RAG" in ent_names

    # 이재원 엔티티에 evidence_of 엣지가 최소 2개 (2개 fact에 등장)
    lee_id = next(e.id for e in ents if e.text == "이재원")
    lee_edges = store.edges_of(lee_id)
    ev_count = sum(1 for e in lee_edges if e.kind == "evidence_of")
    assert ev_count >= 2

    # 이재원 ↔ OptiREC co_occurs 엣지 존재
    optirec_id = next(e.id for e in ents if e.text == "OptiREC")
    pair_exists = any(
        e.kind == "co_occurs" and {e.src, e.dst} == {lee_id, optirec_id}
        for e in lee_edges
    )
    assert pair_exists


def test_ingest_empty_dir(store, tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    embedder = DummyEmbedder(dim=8)
    faiss = FaissStore(tmp_path / "emb.faiss", dim=8)

    report = ingest_path(empty, store=store, faiss=faiss, embedder=embedder, root=empty)
    assert report.facts == 0
    assert report.entities == 0
    assert report.edges == 0
