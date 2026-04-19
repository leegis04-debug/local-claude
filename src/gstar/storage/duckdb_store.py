"""DuckDB 기반 G 저장소.

FAISS 는 별도 인덱스 파일. 여기선 노드/엣지/목표/클러스터/창발 이벤트만.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

from gstar.integrity.hash_chain import compute_content_hash
from gstar.schema import Cluster, Edge, EmergenceEvent, Goal, Namespace, Node

_MIGRATIONS = Path(__file__).parent / "migrations.sql"
_MIGRATIONS_V4 = Path(__file__).parent / "migrations_v4.sql"
_MIGRATIONS_V5 = Path(__file__).parent / "migrations_v5.sql"
_MIGRATIONS_V6 = Path(__file__).parent / "migrations_v6.sql"


def _to_utc_naive(ts: datetime) -> datetime:
    """DuckDB 에 바인딩 전 UTC naive 로 정규화.

    DuckDB 는 TIMESTAMP 컬럼에 aware datetime 을 주면 local tz 로 변환 후 tz 를 벗겨
    저장한다. 이 동작이 시스템 tz 에 따라 fetch 시 다른 값을 돌려주므로, 항상 UTC
    naive 로 저장하고 fetch 시 UTC aware 로 되돌린다.
    """
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(timezone.utc).replace(tzinfo=None)


def _from_utc_naive(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    if ts.tzinfo is not None:
        return ts
    return ts.replace(tzinfo=timezone.utc)


class DuckStore:
    """단일 DuckDB 파일 래퍼. DuckDB 기본 connection 은 thread-safe 하지 않으므로
    FastAPI threadpool 에서 공유 시 `Attempted to dereference unique_ptr that is NULL!`
    같은 internal error 가 발생. `self.lock` 을 통해 모든 쿼리를 직렬화한다.
    외부에서 `store.conn.execute` 를 직접 호출하는 모듈(entity/linker 등)은
    `with store.lock:` 으로 감싸야 안전."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self.lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        ddl = _MIGRATIONS.read_text(encoding="utf-8")
        self.conn.execute(ddl)
        if _MIGRATIONS_V4.exists():
            self.conn.execute(_MIGRATIONS_V4.read_text(encoding="utf-8"))
        if _MIGRATIONS_V5.exists():
            self.conn.execute(_MIGRATIONS_V5.read_text(encoding="utf-8"))
        if _MIGRATIONS_V6.exists():
            self.conn.execute(_MIGRATIONS_V6.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.conn.close()

    # ---------- Node ----------

    _NODE_COLS = (
        "id, kind, text, attrs_json, created_at, version, prev_version_id, "
        "source_namespace, content_hash, prev_hash, signer_id, signature"
    )

    def insert_node(self, node: Node) -> None:
        """Integrity Layer: content_hash/prev_hash 자동 계산 후 저장.

        중복 방지: 같은 `(source_namespace, content_hash)` 노드가 이미 있으면 skip
        하고 기존 node.id 를 입력 `node` 에 반영 (dedupe)."""

        # content_hash 비어있으면 계산
        if not node.content_hash:
            node.content_hash = compute_content_hash(node)
        with self.lock:
            # 중복 검사 (같은 ns + content_hash 이미 있으면 기존 id 재사용)
            existing = self.conn.execute(
                "SELECT id FROM node WHERE source_namespace = ? AND content_hash = ? LIMIT 1",
                [node.source_namespace, node.content_hash],
            ).fetchone()
            if existing:
                node.id = existing[0]
                return

            # prev_hash 미지정이면 동일 namespace 의 직전 노드에서 링크
            if node.prev_hash is None:
                prev_row = self.conn.execute(
                    "SELECT content_hash FROM node WHERE source_namespace = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    [node.source_namespace],
                ).fetchone()
                node.prev_hash = prev_row[0] if prev_row else None

            self.conn.execute(
                f"INSERT INTO node ({self._NODE_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    node.id,
                    node.kind,
                    node.text,
                    json.dumps(node.attrs, ensure_ascii=False),
                    _to_utc_naive(node.created_at),
                    node.version,
                    node.prev_version_id,
                    node.source_namespace,
                    node.content_hash,
                    node.prev_hash,
                    node.signer_id,
                    node.signature,
                ],
            )

    def get_node(self, node_id: str) -> Node | None:
        with self.lock:
            try:
                row = self.conn.execute(
                    f"SELECT {self._NODE_COLS} FROM node WHERE id = ?",
                    [node_id],
                ).fetchone()
            except duckdb.InternalException:
                return None
        return _row_to_node(row) if row else None

    def list_nodes(
        self,
        kind: str | None = None,
        namespace: str | None = None,
        limit: int | None = None,
    ) -> list[Node]:
        sql = f"SELECT {self._NODE_COLS} FROM node"
        params: list[Any] = []
        where: list[str] = []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if namespace:
            where.append("source_namespace = ?")
            params.append(namespace)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at, id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_node(r) for r in rows]

    def count_nodes(self) -> int:
        with self.lock:
            try:
                row = self.conn.execute("SELECT COUNT(*) FROM node").fetchone()
            except duckdb.InternalException:
                return -1
        return int(row[0]) if row else 0

    def nodes_by_namespace(self, namespace: str) -> list[Node]:
        """verify_chain 용. created_at, id 순 오름차순."""
        rows = self.conn.execute(
            f"SELECT {self._NODE_COLS} FROM node WHERE source_namespace = ? "
            "ORDER BY created_at, id",
            [namespace],
        ).fetchall()
        return [_row_to_node(r) for r in rows]

    # ---------- Edge ----------

    def insert_edge(self, edge: Edge) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO edge (id, src, dst, kind, weight, evidence_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    edge.id,
                    edge.src,
                    edge.dst,
                    edge.kind,
                    edge.weight,
                    json.dumps(edge.evidence_ids, ensure_ascii=False),
                    edge.created_at,
                ],
            )

    def edges_of(self, node_id: str) -> list[Edge]:
        """해당 노드를 src 또는 dst 로 포함하는 모든 엣지."""
        with self.lock:
            try:
                rows = self.conn.execute(
                    "SELECT id, src, dst, kind, weight, evidence_json, created_at "
                    "FROM edge WHERE src = ? OR dst = ?",
                    [node_id, node_id],
                ).fetchall()
            except duckdb.InternalException:
                return []
        return [_row_to_edge(r) for r in rows]

    def degree(self, node_id: str) -> int:
        """연결선 수. 3연결까지 안정, 4부터 창발."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM edge WHERE src = ? OR dst = ?",
            [node_id, node_id],
        ).fetchone()
        return int(row[0]) if row else 0

    # ---------- Goal ----------

    def insert_goal(self, goal: Goal) -> None:
        self.conn.execute(
            "INSERT INTO goal (id, text, kind, created_at) VALUES (?, ?, ?, ?)",
            [goal.id, goal.text, goal.kind, goal.created_at],
        )

    def get_goal(self, goal_id: str) -> Goal | None:
        row = self.conn.execute(
            "SELECT id, text, kind, created_at FROM goal WHERE id = ?",
            [goal_id],
        ).fetchone()
        if not row:
            return None
        return Goal(id=row[0], text=row[1], kind=row[2], created_at=row[3])

    def list_goals(self) -> list[Goal]:
        rows = self.conn.execute(
            "SELECT id, text, kind, created_at FROM goal ORDER BY created_at"
        ).fetchall()
        return [Goal(id=r[0], text=r[1], kind=r[2], created_at=r[3]) for r in rows]

    # ---------- Cluster ----------

    _CLUSTER_COLS = (
        "id, goal_id, center_node_id, gravity_mean, stability_score, cycle, "
        "created_at, merkle_root"
    )

    def insert_cluster(self, cluster: Cluster, member_gravity: dict[str, float]) -> None:
        self.conn.execute(
            f"INSERT INTO cluster ({self._CLUSTER_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                cluster.id,
                cluster.goal_id,
                cluster.center_node_id,
                cluster.gravity_mean,
                cluster.stability_score,
                cluster.cycle,
                cluster.created_at,
                cluster.merkle_root,
            ],
        )
        for node_id, g in member_gravity.items():
            self.conn.execute(
                "INSERT INTO cluster_member (cluster_id, node_id, gravity) VALUES (?, ?, ?)",
                [cluster.id, node_id, g],
            )

    def update_cluster_merkle(self, cluster_id: str, merkle_root: str) -> None:
        self.conn.execute(
            "UPDATE cluster SET merkle_root = ? WHERE id = ?",
            [merkle_root, cluster_id],
        )

    def clusters_for_goal(self, goal_id: str) -> list[Cluster]:
        rows = self.conn.execute(
            f"SELECT {self._CLUSTER_COLS} FROM cluster WHERE goal_id = ? "
            "ORDER BY cycle DESC, stability_score DESC",
            [goal_id],
        ).fetchall()
        return [_row_to_cluster(r) for r in rows]

    def get_cluster(self, cluster_id: str) -> Cluster | None:
        row = self.conn.execute(
            f"SELECT {self._CLUSTER_COLS} FROM cluster WHERE id = ?",
            [cluster_id],
        ).fetchone()
        return _row_to_cluster(row) if row else None

    def cluster_members(self, cluster_id: str) -> list[tuple[str, float]]:
        rows = self.conn.execute(
            "SELECT node_id, gravity FROM cluster_member "
            "WHERE cluster_id = ? ORDER BY gravity DESC",
            [cluster_id],
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # ---------- Emergence ----------

    def insert_emergence(self, ev: EmergenceEvent) -> None:
        self.conn.execute(
            "INSERT INTO emergence_event "
            "(id, goal_id, trigger_node_id, connected_json, new_node_candidate, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                ev.id,
                ev.goal_id,
                ev.trigger_node_id,
                json.dumps(ev.connected_node_ids, ensure_ascii=False),
                ev.new_node_candidate,
                ev.created_at,
            ],
        )

    def emergence_for_goal(self, goal_id: str) -> list[EmergenceEvent]:
        rows = self.conn.execute(
            "SELECT id, goal_id, trigger_node_id, connected_json, new_node_candidate, created_at "
            "FROM emergence_event WHERE goal_id = ? ORDER BY created_at",
            [goal_id],
        ).fetchall()
        return [
            EmergenceEvent(
                id=r[0],
                goal_id=r[1],
                trigger_node_id=r[2],
                connected_node_ids=json.loads(r[3]),
                new_node_candidate=r[4],
                created_at=r[5],
            )
            for r in rows
        ]

    # ---------- Selection log ----------

    def log_selection(
        self, goal_id: str, cycle: int, entries: Iterable[tuple[str, float, bool]]
    ) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for node_id, gravity, kept in entries:
            self.conn.execute(
                "INSERT INTO selection_log (goal_id, cycle, node_id, gravity, kept, logged_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [goal_id, cycle, node_id, gravity, kept, now],
            )

    def repeat_selection_rate(self, goal_id: str, node_id: str, last_n: int = 5) -> float:
        row = self.conn.execute(
            "SELECT AVG(CASE WHEN kept THEN 1.0 ELSE 0.0 END) "
            "FROM (SELECT kept FROM selection_log "
            "      WHERE goal_id = ? AND node_id = ? ORDER BY cycle DESC LIMIT ?)",
            [goal_id, node_id, last_n],
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    # ---------- Namespace ----------

    def upsert_namespace(self, ns: Namespace) -> None:
        self.conn.execute(
            "INSERT INTO namespace (name, description, is_active, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET description = excluded.description",
            [ns.name, ns.description, ns.is_active, ns.created_at],
        )

    def list_namespaces(self) -> list[Namespace]:
        rows = self.conn.execute(
            "SELECT name, description, is_active, created_at FROM namespace "
            "ORDER BY created_at"
        ).fetchall()
        return [
            Namespace(name=r[0], description=r[1], is_active=r[2], created_at=r[3])
            for r in rows
        ]

    def active_namespace(self) -> str:
        row = self.conn.execute(
            "SELECT name FROM namespace WHERE is_active = TRUE LIMIT 1"
        ).fetchone()
        return row[0] if row else "personal"

    def set_active_namespace(self, name: str) -> None:
        self.conn.execute("UPDATE namespace SET is_active = FALSE")
        self.conn.execute(
            "INSERT INTO namespace (name, description, is_active, created_at) "
            "VALUES (?, '', TRUE, CURRENT_TIMESTAMP) "
            "ON CONFLICT (name) DO UPDATE SET is_active = TRUE",
            [name],
        )


def _row_to_node(row: tuple) -> Node:
    return Node(
        id=row[0],
        kind=row[1],
        text=row[2],
        attrs=json.loads(row[3]),
        created_at=_from_utc_naive(row[4]),
        version=row[5],
        prev_version_id=row[6],
        source_namespace=row[7],
        content_hash=row[8],
        prev_hash=row[9],
        signer_id=row[10],
        signature=row[11],
    )


def _row_to_cluster(row: tuple) -> Cluster:
    return Cluster(
        id=row[0],
        goal_id=row[1],
        center_node_id=row[2],
        gravity_mean=row[3],
        stability_score=row[4],
        cycle=row[5],
        created_at=_from_utc_naive(row[6]),
        merkle_root=row[7],
    )


def _row_to_edge(row: tuple) -> Edge:
    return Edge(
        id=row[0],
        src=row[1],
        dst=row[2],
        kind=row[3],
        weight=row[4],
        evidence_ids=json.loads(row[5]),
        created_at=row[6],
    )
