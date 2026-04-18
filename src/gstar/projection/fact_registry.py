"""Fact 레지스트리 — 단계 간 수치·엔티티 일관성 관리.

MCP validator 의 fact_registry 자기충돌 폭증 버그를 회피하기 위해 projection 내부
registry 로 분리. DuckDB `projection_fact` 테이블 사용.

- `upsert()`: 중복 제거 후 저장, 충돌(같은 entity 의 다른 metric 값 등) 감지
- `query()`: 트랙·엔티티·kind 별 조회
- `detect_conflicts()`: 신규 fact vs 기존 fact 비교
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from gstar.entity.graph import connected_facts
from gstar.entity.types import EntityKind
from gstar.projection.summarizer import Fact
from gstar.storage.duckdb_store import DuckStore


@dataclass
class Conflict:
    kind: str                     # "metric_diverge" | "entity_mismatch" | ...
    description: str
    fact_a: Fact
    fact_b: Fact | None = None
    severity: str = "warn"        # "warn" | "error"


@dataclass
class MergeReport:
    inserted: int
    duplicated: int
    conflicts: list[Conflict] = field(default_factory=list)


_METRIC_VAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%?")


def _fact_id(f: Fact) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(f.source_hash.encode())
    h.update(b"|")
    h.update(f.text.encode("utf-8"))
    return h.hexdigest()[:24]


def _ts() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _exists(store: DuckStore, fact_id: str) -> bool:
    row = store.conn.execute(
        "SELECT 1 FROM projection_fact WHERE id = ?", [fact_id]
    ).fetchone()
    return row is not None


def _metric_value(text: str) -> float | None:
    m = _METRIC_VAL_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _same_entity_overlap(a: Fact, b: Fact) -> bool:
    return bool(set(a.entity_ids) & set(b.entity_ids))


def detect_conflicts(
    new: Fact,
    existing: list[Fact],
    *,
    metric_tolerance_pct: float = 5.0,
) -> list[Conflict]:
    """신규 fact vs 기존 fact 목록 → 충돌 리스트."""
    conflicts: list[Conflict] = []
    if new.kind == "metric":
        new_val = _metric_value(new.text)
        if new_val is None:
            return conflicts
        for old in existing:
            if old.kind != "metric":
                continue
            if not _same_entity_overlap(new, old) and new.text.split()[0] != old.text.split()[0]:
                continue
            old_val = _metric_value(old.text)
            if old_val is None:
                continue
            if abs(old_val - new_val) / max(1e-9, max(abs(old_val), abs(new_val))) * 100 >= metric_tolerance_pct:
                conflicts.append(
                    Conflict(
                        kind="metric_diverge",
                        description=(
                            f"같은 지표의 수치 불일치: {old_val} (단계 {old.source_stage}) "
                            f"vs {new_val} (단계 {new.source_stage})"
                        ),
                        fact_a=new,
                        fact_b=old,
                        severity="warn",
                    )
                )
    return conflicts


def upsert(
    facts: Iterable[Fact],
    store: DuckStore,
    project_id: str,
    *,
    track: str,
    detect_conflict: bool = True,
) -> MergeReport:
    inserted = 0
    duplicated = 0
    conflicts_all: list[Conflict] = []
    existing_by_kind: dict[str, list[Fact]] = {}

    if detect_conflict:
        rows = store.conn.execute(
            "SELECT kind, text, entity_ids_json, source_stage, source_hash "
            "FROM projection_fact WHERE project_id = ? AND track = ?",
            [project_id, track],
        ).fetchall()
        for row in rows:
            f = Fact(
                text=row[1],
                kind=row[0],
                source_stage=row[3],
                source_hash=row[4],
                entity_ids=json.loads(row[2] or "[]"),
                track=track,
            )
            existing_by_kind.setdefault(f.kind, []).append(f)

    for f in facts:
        fid = _fact_id(f)
        if _exists(store, fid):
            duplicated += 1
            continue
        if detect_conflict:
            existing = existing_by_kind.get(f.kind, [])
            cs = detect_conflicts(f, existing)
            conflicts_all.extend(cs)
        store.conn.execute(
            "INSERT INTO projection_fact "
            "(id, project_id, track, kind, text, entity_ids_json, source_stage, source_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                fid,
                project_id,
                track,
                f.kind,
                f.text,
                json.dumps(f.entity_ids, ensure_ascii=False),
                f.source_stage,
                f.source_hash,
                _ts(),
            ],
        )
        existing_by_kind.setdefault(f.kind, []).append(f)
        inserted += 1

    return MergeReport(inserted=inserted, duplicated=duplicated, conflicts=conflicts_all)


def query(
    store: DuckStore,
    project_id: str,
    *,
    track: str | None = None,
    kind: str | None = None,
    entity_id: str | None = None,
    stage: str | None = None,
) -> list[Fact]:
    sql = (
        "SELECT kind, text, entity_ids_json, source_stage, source_hash, track "
        "FROM projection_fact WHERE project_id = ?"
    )
    params: list = [project_id]
    if track:
        sql += " AND track = ?"
        params.append(track)
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    if stage:
        sql += " AND source_stage = ?"
        params.append(stage)
    sql += " ORDER BY created_at"
    rows = store.conn.execute(sql, params).fetchall()
    out: list[Fact] = []
    for row in rows:
        entity_ids = json.loads(row[2] or "[]")
        if entity_id and entity_id not in entity_ids:
            continue
        out.append(
            Fact(
                text=row[1],
                kind=row[0],
                source_stage=row[3],
                source_hash=row[4],
                entity_ids=entity_ids,
                track=row[5] or track or "",
            )
        )
    return out


def mentions_entity(store: DuckStore, project_id: str, track: str, entity_id: str) -> list[Fact]:
    return query(store, project_id, track=track, entity_id=entity_id)


def facts_for_entity(
    entity_id: str,
    store: DuckStore,
) -> list[str]:
    """entity_id → 연결된 fact node id 리스트 (Phase A graph 활용)."""
    refs = connected_facts(entity_id, store)
    return [r.fact_id for r in refs]


def clear_project(store: DuckStore, project_id: str, track: str | None = None) -> int:
    sql = "DELETE FROM projection_fact WHERE project_id = ?"
    params: list = [project_id]
    if track:
        sql += " AND track = ?"
        params.append(track)
    before = store.conn.execute(
        "SELECT COUNT(*) FROM projection_fact WHERE project_id = ?"
        + (" AND track = ?" if track else ""),
        params,
    ).fetchone()[0]
    store.conn.execute(sql, params)
    return int(before)
