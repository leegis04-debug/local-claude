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
_MIGRATIONS_V7 = Path(__file__).parent / "migrations_v7.sql"


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
    """단일 DuckDB 파일 래퍼.

    동시성 모델:
    - `self.conn` — 단일 writer connection. 쓰기 경로는 `with self.lock:` 으로 직렬화.
    - `self._read_conn()` — 쓰레드-로컬 read-only connection. FastAPI threadpool 에서
      여러 쓰레드가 동시에 /search 요청을 받을 때 각자 독립 커서를 쓰도록 분리.
      DuckDB 단일 connection 의 pending cursor 가 다른 쓰레드 execute 와 충돌해
      `Attempting to execute an unsuccessful or closed pending query result` 를 띄우던
      문제(gstar/gravity 경로)를 해결한다.

    외부에서 `store.conn.execute` 를 직접 호출하는 모듈(entity/linker 등)은 writer
    경로이므로 `with store.lock:` 으로 감싸야 안전.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self.lock = threading.RLock()
        self._tls = threading.local()
        self._migrate()

    def _read_conn(self) -> "duckdb.DuckDBPyConnection":
        """쓰레드별 독립 cursor. FastAPI threadpool 에서 /search 동시 read 가
        `self.conn` 단일 커서를 공유하다 race 로 크래시하던 것을 방지한다.

        `self.conn.cursor()` 는 같은 DB connection context 안에서 독립적인
        execute/fetch 상태를 가진 핸들을 반환한다. 별도 `duckdb.connect(path,
        read_only=True)` 는 DuckDB 의 "same file different configuration"
        제약으로 write connection 과 공존 불가이므로 사용하지 않는다.
        """
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = self.conn.cursor()
            self._tls.conn = conn
        return conn

    def _migrate(self) -> None:
        ddl = _MIGRATIONS.read_text(encoding="utf-8")
        self.conn.execute(ddl)
        if _MIGRATIONS_V4.exists():
            self.conn.execute(_MIGRATIONS_V4.read_text(encoding="utf-8"))
        if _MIGRATIONS_V5.exists():
            self.conn.execute(_MIGRATIONS_V5.read_text(encoding="utf-8"))
        if _MIGRATIONS_V6.exists():
            self.conn.execute(_MIGRATIONS_V6.read_text(encoding="utf-8"))
        if _MIGRATIONS_V7.exists():
            self.conn.execute(_MIGRATIONS_V7.read_text(encoding="utf-8"))

    def close(self) -> None:
        self.conn.close()

    # ---------- Node ----------

    _NODE_COLS = (
        "id, kind, text, attrs_json, created_at, version, prev_version_id, "
        "source_namespace, content_hash, prev_hash, signer_id, signature"
    )

    def insert_node(self, node: Node) -> None:
        """Integrity Layer: content_hash/prev_hash 자동 계산 후 저장.

        중복 방지: 같은 `(source_namespace, text)` 노드가 이미 있으면 skip 하고
        기존 node.id 를 입력 `node` 에 반영. content_hash 는 node.id/created_at 을
        포함하므로 재ingest 마다 달라 dedupe 키로 부적합 → text 기반.

        chunker 는 파일의 섹션/문단 단위로 쪼개므로 텍스트가 충분히 길어 중복 희박.
        명시적 fresh insert 가 필요하면 `allow_dedup=False` 로 호출할 수 있도록
        추후 확장 가능."""

        # content_hash 비어있으면 계산
        if not node.content_hash:
            node.content_hash = compute_content_hash(node)
        with self.lock:
            # 중복 검사 (같은 ns + 완전 동일 text 이미 있으면 기존 id 재사용)
            existing = self.conn.execute(
                "SELECT id FROM node WHERE source_namespace = ? AND text = ? LIMIT 1",
                [node.source_namespace, node.text],
            ).fetchone()
            if existing:
                node.id = existing[0]
                return

            # PK id 충돌 방어: 같은 ULID 가 이미 존재하면 새 ULID 재할당 후 재시도
            # (ULID 생성 라이브러리 race·시계 해상도 엣지케이스)
            try:
                from ulid import ULID as _ULID
            except Exception:
                _ULID = None
            for _retry in range(3):
                row_pk = self.conn.execute(
                    "SELECT 1 FROM node WHERE id = ?", [node.id]
                ).fetchone()
                if row_pk is None:
                    break
                if _ULID is None:
                    raise RuntimeError(
                        f"node.id 충돌 {node.id} 인데 ulid 재할당 불가"
                    )
                node.id = str(_ULID())
                # content_hash 는 id 의존 — 재계산
                node.content_hash = compute_content_hash(node)
            else:
                raise RuntimeError(
                    f"node.id 충돌 3회 재시도 실패 {node.id}"
                )

            # prev_hash 미지정이면 동일 namespace 의 직전 노드에서 링크
            if node.prev_hash is None:
                prev_row = self.conn.execute(
                    "SELECT content_hash FROM node WHERE source_namespace = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1",
                    [node.source_namespace],
                ).fetchone()
                node.prev_hash = prev_row[0] if prev_row else None

            self.conn.execute(
                f"INSERT INTO node ({self._NODE_COLS}) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
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
        try:
            row = self._read_conn().execute(
                f"SELECT {self._NODE_COLS} FROM node WHERE id = ?",
                [node_id],
            ).fetchone()
        except duckdb.InternalException:
            return None
        return _row_to_node(row) if row else None

    def get_nodes_many(self, node_ids: list[str]) -> dict[str, Node]:
        """`get_node` 의 batch 버전. compute_gravity 의 후보 N개 fetch 를
        1회 쿼리로 합친다. 존재하지 않는 id 는 결과 dict 에 key 없음."""
        if not node_ids:
            return {}
        uniq = list(set(node_ids))
        placeholders = ",".join(["?"] * len(uniq))
        sql = f"SELECT {self._NODE_COLS} FROM node WHERE id IN ({placeholders})"
        try:
            rows = self._read_conn().execute(sql, uniq).fetchall()
        except duckdb.InternalException:
            return {}
        out: dict[str, Node] = {}
        for r in rows:
            n = _row_to_node(r)
            out[n.id] = n
        return out

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
        rows = self._read_conn().execute(sql, params).fetchall()
        return [_row_to_node(r) for r in rows]

    def count_nodes(self) -> int:
        try:
            row = self._read_conn().execute("SELECT COUNT(*) FROM node").fetchone()
        except duckdb.InternalException:
            return -1
        return int(row[0]) if row else 0

    def nodes_by_namespace(self, namespace: str) -> list[Node]:
        """verify_chain 용. created_at, id 순 오름차순."""
        rows = self._read_conn().execute(
            f"SELECT {self._NODE_COLS} FROM node WHERE source_namespace = ? "
            "ORDER BY created_at, id",
            [namespace],
        ).fetchall()
        return [_row_to_node(r) for r in rows]

    # ---------- Edge ----------

    def insert_edge(self, edge: Edge) -> None:
        with self.lock:
            # PK id 충돌 방어 (ULID 재할당 최대 3회)
            try:
                from ulid import ULID as _ULID
            except Exception:
                _ULID = None
            for _retry in range(3):
                row = self.conn.execute(
                    "SELECT 1 FROM edge WHERE id = ?", [edge.id]
                ).fetchone()
                if row is None:
                    break
                if _ULID is None:
                    raise RuntimeError(f"edge.id 충돌 {edge.id} 인데 ulid 재할당 불가")
                edge.id = str(_ULID())
            else:
                raise RuntimeError(f"edge.id 충돌 3회 재시도 실패 {edge.id}")

            self.conn.execute(
                "INSERT INTO edge (id, src, dst, kind, weight, evidence_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
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
        try:
            rows = self._read_conn().execute(
                "SELECT id, src, dst, kind, weight, evidence_json, created_at "
                "FROM edge WHERE src = ? OR dst = ?",
                [node_id, node_id],
            ).fetchall()
        except duckdb.InternalException:
            return []
        return [_row_to_edge(r) for r in rows]

    def edges_of_many(self, node_ids: list[str]) -> dict[str, list[Edge]]:
        """`edges_of` 의 batch 버전. 여러 노드의 엣지를 한 번의 쿼리로 가져와
        compute_gravity 의 N 회 DB 왕복을 1 회로 줄인다.

        반환: {node_id: [edge, ...]}. 입력에 없는 id 는 key 없음. 방향 무관 —
        한 edge 가 `src`/`dst` 양쪽에 매칭되는 경우 양쪽 key 의 리스트에 들어간다
        (`edges_of` 와 동일 동작)."""
        if not node_ids:
            return {}
        uniq = list(set(node_ids))
        placeholders = ",".join(["?"] * len(uniq))
        sql = (
            "SELECT id, src, dst, kind, weight, evidence_json, created_at "
            f"FROM edge WHERE src IN ({placeholders}) OR dst IN ({placeholders})"
        )
        try:
            rows = self._read_conn().execute(sql, uniq + uniq).fetchall()
        except duckdb.InternalException:
            return {}
        id_set = set(uniq)
        result: dict[str, list[Edge]] = {nid: [] for nid in uniq}
        for r in rows:
            edge = _row_to_edge(r)
            if edge.src in id_set:
                result[edge.src].append(edge)
            if edge.dst in id_set and edge.dst != edge.src:
                result[edge.dst].append(edge)
        return result

    def degree(self, node_id: str) -> int:
        """연결선 수. 3연결까지 안정, 4부터 창발."""
        row = self._read_conn().execute(
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
        row = self._read_conn().execute(
            "SELECT id, text, kind, created_at FROM goal WHERE id = ?",
            [goal_id],
        ).fetchone()
        if not row:
            return None
        return Goal(id=row[0], text=row[1], kind=row[2], created_at=row[3])

    def list_goals(self) -> list[Goal]:
        rows = self._read_conn().execute(
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
        rows = self._read_conn().execute(
            f"SELECT {self._CLUSTER_COLS} FROM cluster WHERE goal_id = ? "
            "ORDER BY cycle DESC, stability_score DESC",
            [goal_id],
        ).fetchall()
        return [_row_to_cluster(r) for r in rows]

    def get_cluster(self, cluster_id: str) -> Cluster | None:
        row = self._read_conn().execute(
            f"SELECT {self._CLUSTER_COLS} FROM cluster WHERE id = ?",
            [cluster_id],
        ).fetchone()
        return _row_to_cluster(row) if row else None

    def cluster_members(self, cluster_id: str) -> list[tuple[str, float]]:
        rows = self._read_conn().execute(
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
        rows = self._read_conn().execute(
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
        row = self._read_conn().execute(
            "SELECT AVG(CASE WHEN kept THEN 1.0 ELSE 0.0 END) "
            "FROM (SELECT kept FROM selection_log "
            "      WHERE goal_id = ? AND node_id = ? ORDER BY cycle DESC LIMIT ?)",
            [goal_id, node_id, last_n],
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0

    def repeat_selection_rate_many(
        self, goal_id: str, node_ids: list[str], last_n: int = 5
    ) -> dict[str, float]:
        """`repeat_selection_rate` 의 batch 버전. compute_gravity 가 후보 N개에
        대해 각각 호출하던 것을 한 번의 window-function 쿼리로 합쳐 DB 왕복을 제거.

        반환: {node_id: rate}. selection_log 에 기록이 없는 node 는 0.0."""
        if not node_ids:
            return {}
        uniq = list(set(node_ids))
        placeholders = ",".join(["?"] * len(uniq))
        sql = (
            "SELECT node_id, AVG(CASE WHEN kept THEN 1.0 ELSE 0.0 END) "
            "FROM ("
            "  SELECT node_id, kept, "
            "         ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY cycle DESC) AS rn "
            f"  FROM selection_log WHERE goal_id = ? AND node_id IN ({placeholders})"
            ") WHERE rn <= ? GROUP BY node_id"
        )
        params: list[Any] = [goal_id] + uniq + [last_n]
        try:
            rows = self._read_conn().execute(sql, params).fetchall()
        except duckdb.InternalException:
            return {nid: 0.0 for nid in uniq}
        out: dict[str, float] = {nid: 0.0 for nid in uniq}
        for nid, avg in rows:
            out[nid] = float(avg) if avg is not None else 0.0
        return out

    # ---------- Namespace ----------

    def upsert_namespace(self, ns: Namespace) -> None:
        self.conn.execute(
            "INSERT INTO namespace (name, description, is_active, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET description = excluded.description",
            [ns.name, ns.description, ns.is_active, ns.created_at],
        )

    def list_namespaces(self) -> list[Namespace]:
        rows = self._read_conn().execute(
            "SELECT name, description, is_active, created_at FROM namespace "
            "ORDER BY created_at"
        ).fetchall()
        return [
            Namespace(name=r[0], description=r[1], is_active=r[2], created_at=r[3])
            for r in rows
        ]

    def active_namespace(self) -> str:
        row = self._read_conn().execute(
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
