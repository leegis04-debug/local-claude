"""ingest 가 미등록 namespace 를 자동 upsert 하는지 검증 (Week 7.2 연동)."""

from __future__ import annotations

from pathlib import Path

from gstar.embedding import DummyEmbedder
from gstar.ingest.pipeline import ingest_path
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


def test_ingest_auto_registers_unknown_namespace(tmp_path: Path):
    store = DuckStore(tmp_path / "g.duckdb")
    try:
        before = {n.name for n in store.list_namespaces()}
        assert "newcorp" not in before

        md = tmp_path / "note.md"
        md.write_text(
            "이재원은 다겸의 책임연구원이다.\nOptiREC 는 RAG 기반이다.\n",
            encoding="utf-8",
        )
        embedder = DummyEmbedder(dim=8)
        faiss = FaissStore(tmp_path / "emb.faiss", dim=8)
        ingest_path(md, store=store, faiss=faiss, embedder=embedder,
                    root=tmp_path, namespace="newcorp")

        after = {n.name for n in store.list_namespaces()}
        assert "newcorp" in after

        # 같은 namespace 로 재실행해도 중복 에러 없이 upsert 유지
        ingest_path(md, store=store, faiss=faiss, embedder=embedder,
                    root=tmp_path, namespace="newcorp")
        assert sum(1 for n in store.list_namespaces() if n.name == "newcorp") == 1
    finally:
        store.close()
