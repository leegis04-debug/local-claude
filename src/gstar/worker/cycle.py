"""Worker tick — Phase B1.

한 주기 단위:
  1. pause 체크 (B2)
  2. community detection (Louvain) — 큰 project_id 만
  3. (future) stellar_packer, reinforce cycle, stats
  4. worker_tick 테이블에 결과 기록

Pause: `${GSTAR_HOME}/worker.paused` 파일이 존재하면 전체 skip.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ulid import ULID


def _gstar_home() -> Path:
    return Path(os.environ.get("GSTAR_HOME", str(Path.home() / ".gstar")))


def pause_flag_path() -> Path:
    return _gstar_home() / "worker.paused"


def is_paused() -> bool:
    return pause_flag_path().exists()


def set_paused(flag: bool) -> None:
    p = pause_flag_path()
    if flag:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch(exist_ok=True)
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


@dataclass
class TickReport:
    tick_id: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int = 0
    status: str = "running"         # running | success | failed | paused
    steps: dict = field(default_factory=dict)   # {"community": {...}}
    error: str | None = None

    def to_json(self) -> dict:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        if self.finished_at is not None:
            d["finished_at"] = self.finished_at.isoformat()
        return d


def _write_tick(store, rep: TickReport) -> None:
    with store.lock:
        try:
            # 업데이트 또는 신규
            existing = store.conn.execute(
                "SELECT id FROM worker_tick WHERE id = ?", [rep.tick_id]
            ).fetchone()
            if existing:
                store.conn.execute(
                    "UPDATE worker_tick SET finished_at=?, duration_ms=?, status=?, "
                    "steps_json=?, error=? WHERE id=?",
                    [
                        rep.finished_at,
                        rep.duration_ms,
                        rep.status,
                        json.dumps(rep.steps, ensure_ascii=False, default=str),
                        rep.error,
                        rep.tick_id,
                    ],
                )
            else:
                store.conn.execute(
                    "INSERT INTO worker_tick "
                    "(id, started_at, finished_at, duration_ms, status, steps_json, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        rep.tick_id,
                        rep.started_at,
                        rep.finished_at,
                        rep.duration_ms,
                        rep.status,
                        json.dumps(rep.steps, ensure_ascii=False, default=str),
                        rep.error,
                    ],
                )
        except Exception:
            pass  # tick 기록 실패가 워커 자체를 막지 않음


def run_cycle(
    store,
    *,
    min_community_size: int = 3,
    project_ids: list[str] | None = None,
    skip_if_paused: bool = True,
    mode: str = "per_project",
    mine_traces: bool = True,
    embedder=None,
    faiss=None,
) -> TickReport:
    """Worker 한 주기 실행.

    project_ids 를 지정하면 해당 project_id 만 community detection.
    None 이면 전체 project_id 순회 (대용량 주의).
    """
    from gstar.worker.community import detect_louvain

    tick_id = str(ULID())
    started_at = datetime.now(timezone.utc)
    rep = TickReport(tick_id=tick_id, started_at=started_at, status="running")
    _write_tick(store, rep)

    if skip_if_paused and is_paused():
        rep.status = "paused"
        rep.finished_at = datetime.now(timezone.utc)
        rep.duration_ms = int((rep.finished_at - started_at).total_seconds() * 1000)
        _write_tick(store, rep)
        return rep

    t0 = time.time()
    try:
        community_summaries = []
        if mode == "global":
            results = detect_louvain(
                store, min_community_size=min_community_size, mode="global"
            )
        elif project_ids is None:
            results = detect_louvain(store, min_community_size=min_community_size)
        else:
            results = []
            for pid in project_ids:
                results.extend(
                    detect_louvain(store, project_id=pid, min_community_size=min_community_size)
                )
        for r in results:
            community_summaries.append(
                {
                    "project_id": r.project_id,
                    "algorithm": r.algorithm,
                    "communities": r.communities,
                    "members_total": r.members_total,
                    "skipped_small": r.skipped_small,
                    "updated_entities": r.updated_entities,
                }
            )
        rep.steps["community"] = {
            "count": sum(s["communities"] for s in community_summaries),
            "projects": len(community_summaries),
            "details": community_summaries,
        }

        # Phase G6 — trace → procedure 패턴 추출
        if mine_traces:
            try:
                from gstar.worker.procedures import mine_procedures
                mres = mine_procedures(store, embedder=embedder, faiss=faiss)
                rep.steps["procedures"] = {
                    "tasks_scanned": mres.tasks_scanned,
                    "created": mres.procedures_created,
                    "skipped_existing": mres.procedures_skipped_existing,
                    "errors": mres.errors,
                }
            except Exception as exc:
                rep.steps["procedures"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Phase H5 — G → Qdrant 파생 뷰 emit (embedder 있을 때만)
        if embedder is not None and os.environ.get("QDRANT_MIRROR_ENABLED", "on").lower() in {"on", "1", "true"}:
            try:
                from gstar.mirror.qdrant_view import mirror_to_qdrant
                mirror_limit = int(os.environ.get("QDRANT_MIRROR_LIMIT", "1000"))
                qres = mirror_to_qdrant(store, embedder, limit=mirror_limit)
                rep.steps["qdrant_mirror"] = {
                    "collection": qres.collection,
                    "scanned": qres.scanned,
                    "upserted": qres.upserted,
                    "failed": qres.failed,
                    "errors": qres.errors[:3],
                }
            except Exception as exc:
                rep.steps["qdrant_mirror"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Phase H6 — G → Neo4j 파생 뷰 emit
        if os.environ.get("NEO4J_MIRROR_ENABLED", "on").lower() in {"on", "1", "true"}:
            try:
                from gstar.mirror.neo4j_view import mirror_to_neo4j
                nlim = int(os.environ.get("NEO4J_MIRROR_NODES", "200"))
                elim = int(os.environ.get("NEO4J_MIRROR_EDGES", "500"))
                nres = mirror_to_neo4j(store, limit_nodes=nlim, limit_edges=elim)
                rep.steps["neo4j_mirror"] = {
                    "nodes_scanned": nres.scanned_nodes,
                    "nodes_upserted": nres.upserted_nodes,
                    "edges_scanned": nres.scanned_edges,
                    "edges_upserted": nres.upserted_edges,
                    "failed": nres.failed,
                    "errors": nres.errors[:3],
                }
            except Exception as exc:
                rep.steps["neo4j_mirror"] = {"error": f"{type(exc).__name__}: {exc}"}

        # Phase H9 — G → legacy bridge (신뢰도 3등급)
        if os.environ.get("LEGACY_BRIDGE_ENABLED", "on").lower() in {"on", "1", "true"}:
            try:
                from gstar.mirror.legacy_bridge import bridge_g_to_legacy
                lim = int(os.environ.get("LEGACY_BRIDGE_LIMIT", "500"))
                lres = bridge_g_to_legacy(store, limit=lim)
                rep.steps["legacy_bridge"] = {
                    "scanned": lres.scanned,
                    "high": lres.high,
                    "medium": lres.medium,
                    "low": lres.low,
                    "errors": lres.errors,
                }
            except Exception as exc:
                rep.steps["legacy_bridge"] = {"error": f"{type(exc).__name__}: {exc}"}

        rep.status = "success"
    except Exception as exc:
        rep.status = "failed"
        rep.error = f"{type(exc).__name__}: {exc}"

    rep.finished_at = datetime.now(timezone.utc)
    rep.duration_ms = int((time.time() - t0) * 1000)
    _write_tick(store, rep)
    return rep
