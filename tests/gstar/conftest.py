"""공통 fixture. 각 테스트마다 임시 GSTAR_HOME 을 만든다."""

from __future__ import annotations

from pathlib import Path

import pytest

from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


@pytest.fixture
def store(tmp_path: Path) -> DuckStore:
    db = tmp_path / "g.duckdb"
    s = DuckStore(db)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def faiss_store(tmp_path: Path) -> FaissStore:
    idx = tmp_path / "emb.faiss"
    return FaissStore(idx, dim=8)
