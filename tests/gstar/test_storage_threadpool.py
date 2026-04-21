"""DuckStore 쓰레드-로컬 cursor 동시성 테스트.

기존 구조는 `self.conn` 단일 cursor 를 FastAPI threadpool 쓰레드가 공유하면서
`_duckdb.InvalidInputException: Attempting to execute an unsuccessful or closed
pending query result` 로 /search 엔드포인트가 크래시했다. `_read_conn()` 이
쓰레드별 독립 cursor 를 주도록 패치된 후 회귀 방지용.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from gstar.schema import Edge, Node
from gstar.storage.duckdb_store import DuckStore


def _seed(store: DuckStore, n: int = 40) -> list[str]:
    ids: list[str] = []
    for i in range(n):
        node = Node(kind="fact", text=f"n{i}", source_namespace="t")
        store.insert_node(node)
        ids.append(node.id)
    for i in range(0, n - 1, 2):
        store.insert_edge(Edge(src=ids[i], dst=ids[i + 1], kind="rel", weight=1.0))
    return ids


def test_concurrent_reads_do_not_crash(tmp_path: Path):
    store = DuckStore(tmp_path / "g.duckdb")
    try:
        ids = _seed(store, n=40)

        def worker(nid: str) -> int:
            node = store.get_node(nid)
            edges = store.edges_of(nid)
            deg = store.degree(nid)
            rate = store.repeat_selection_rate("goal-x", nid, last_n=5)
            assert node is not None
            assert isinstance(edges, list)
            return deg + int(rate * 100)

        with ThreadPoolExecutor(max_workers=16) as ex:
            results = list(ex.map(worker, ids * 8))  # 320 concurrent ops

        assert len(results) == 320
    finally:
        store.close()
