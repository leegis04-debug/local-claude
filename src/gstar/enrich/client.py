"""맥북 측 비차단 enrich subscriber.

Fire-and-forget 패턴:
- selection_loop 이 cycle 0 시작 시 `start(request)` 호출
- 백그라운드 스레드가 4090 `/enrich/stream` SSE 구독, 큐에 fact 적재
- 매 cycle 시작에 `drain(queue, store, ...)` 호출해서 도착분 insert
- `stop()` 으로 종료
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterator

from gstar.enrich.policy import EnrichItem, EnrichRequest


class EnrichSubscriber:
    """백그라운드 SSE 구독자. httpx.stream 을 사용."""

    def __init__(
        self,
        base_url: str,
        *,
        queue_maxsize: int = 500,
        timeout_s: float = 300.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.queue: "queue.Queue[EnrichItem]" = queue.Queue(maxsize=queue_maxsize)
        self.timeout_s = timeout_s
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._error: Exception | None = None

    def start(self, request: EnrichRequest) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run, args=(request,), daemon=True, name="enrich-subscriber"
        )
        self._thread.start()

    def stop(self, wait_s: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=wait_s)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> Exception | None:
        return self._error

    def _run(self, request: EnrichRequest) -> None:
        try:
            import httpx

            payload = asdict(request)
            with httpx.stream(
                "POST",
                f"{self.base_url}/enrich/stream",
                json=payload,
                timeout=self.timeout_s,
            ) as r:
                r.raise_for_status()
                for line in r.iter_lines():
                    if self._stop.is_set():
                        break
                    if not line or not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        item = EnrichItem(**data)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    try:
                        self.queue.put(item, block=False)
                    except queue.Full:
                        continue
        except Exception as e:
            self._error = e


@dataclass
class DrainReport:
    facts_inserted: int = 0
    edges_inserted: int = 0
    dropped_duplicate: int = 0
    dropped_invalid: int = 0


def drain_queue(
    q: "queue.Queue[EnrichItem]",
    store,                             # DuckStore
    embedder,                          # Embedder | None
    *,
    namespace: str = "web",
    hash_checker: Callable[[str], bool] | None = None,
    max_items: int = 100,
) -> DrainReport:
    """큐에 도착한 EnrichItem 들을 DB에 fact + edge 로 insert.

    hash_checker: 이미 존재하는 content_hash 확인 함수 (없으면 중복 미체크).
    """
    from gstar.enrich.policy import hash_text, should_insert
    from gstar.schema import Edge as SchemaEdge
    from gstar.schema import Node

    report = DrainReport()
    processed = 0
    while processed < max_items:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        processed += 1

        if not should_insert(item.fact_text, set()):
            report.dropped_invalid += 1
            continue

        content_hash = item.content_hash or hash_text(item.fact_text)
        if hash_checker is not None and hash_checker(content_hash):
            report.dropped_duplicate += 1
            continue

        node = Node(
            kind=item.fact_kind if item.fact_kind in ("fact", "entity") else "fact",
            text=item.fact_text,
            attrs={
                "source_url": item.source_url,
                "query": item.query,
                "confidence": item.confidence,
                "fetched_via": "enrich",
            },
            source_namespace=namespace,
        )
        store.insert_node(node)
        if embedder is not None:
            try:
                vec = embedder.encode([node.text])[0]
                # faiss 인덱스는 호출자가 관리 (pipeline 과 동일 pattern)
                node._embedding_vec = vec  # type: ignore[attr-defined]
            except Exception:
                pass
        report.facts_inserted += 1

        for attach_id in item.attach_to_node_ids:
            try:
                store.insert_edge(
                    SchemaEdge(
                        src=attach_id,
                        dst=node.id,
                        kind=item.edge_kind or "evidence_of",
                        weight=float(item.confidence),
                        evidence_ids=[],
                    )
                )
                report.edges_inserted += 1
            except Exception:
                continue

    return report


class MockEnrichStream:
    """테스트·오프라인용. 4090 없이 고정 EnrichItem 리스트를 큐에 주입."""

    def __init__(self, items: list[EnrichItem]):
        self.queue: "queue.Queue[EnrichItem]" = queue.Queue()
        self._items = list(items)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, request: EnrichRequest) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, wait_s: float = 0.5) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=wait_s)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> Exception | None:
        return None

    def _run(self) -> None:
        for item in self._items:
            if self._stop.is_set():
                break
            try:
                self.queue.put(item, block=False)
            except queue.Full:
                continue
            time.sleep(0.01)
