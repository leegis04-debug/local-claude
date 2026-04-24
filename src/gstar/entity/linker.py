"""Surface form → Canonical entity 링킹.

jw wrapper 의 `entity_extract._strip_kr_postposition` 이 놓치던 조사 변형을
`normalizer.py` (kiwipiepy) + 별칭 학습으로 해결.

우선순위:
1. entity_alias 테이블 일치 (이미 학습된 variant → canonical)
2. lemma 완전 일치 (normalizer 결과)
3. 편집거리 ≤ 2 (한국어만, 영어는 소문자 비교)
4. 신규 canonical 생성 + 현재 surface 를 alias 로 등록
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from gstar.entity.normalizer import extract_content_terms
from gstar.entity.types import CanonicalEntity, EntityKind, Track
from gstar.storage.duckdb_store import DuckStore


@dataclass
class LinkResult:
    entity: CanonicalEntity
    matched_by: str           # "alias" | "lemma" | "fuzzy" | "created"
    is_new: bool


def _ulid() -> str:
    try:
        from ulid import ULID
        return str(ULID())
    except Exception:
        from uuid import uuid4
        return uuid4().hex


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]


def _canonical_lemma(surface: str) -> str:
    """영문은 그대로, 한글은 체언 병합."""
    stripped = surface.strip()
    if not stripped:
        return ""
    has_hangul = any("\uac00" <= c <= "\ud7a3" for c in stripped)
    if not has_hangul:
        return stripped
    terms = extract_content_terms(stripped, join_adjacent=True)
    if terms:
        return max(terms, key=len)
    return stripped


def _ts() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _row_to_canonical(row, aliases: list[str] | None = None) -> CanonicalEntity:
    return CanonicalEntity(
        id=row[0],
        project_id=row[1],
        track=Track(row[2]),
        canonical_name=row[3],
        kind=EntityKind(row[4]),
        scope=row[5],
        mentions=int(row[6] or 0),
        aliases=aliases or [],
    )


_CANON_COLS = "id, project_id, track, canonical_name, kind, scope, mentions"


def _fetch_aliases(store: DuckStore, project_id: str, track: str, canonical_id: str) -> list[str]:
    rows = store.conn.execute(
        "SELECT alias_surface FROM entity_alias "
        "WHERE project_id = ? AND track = ? AND canonical_id = ? "
        "ORDER BY first_seen",
        [project_id, track, canonical_id],
    ).fetchall()
    return [r[0] for r in rows]


def _by_alias(
    store: DuckStore, surface: str, project_id: str, track: str
) -> CanonicalEntity | None:
    row = store.conn.execute(
        "SELECT canonical_id FROM entity_alias "
        "WHERE project_id = ? AND track = ? AND alias_surface = ?",
        [project_id, track, surface],
    ).fetchone()
    if not row:
        return None
    canon_row = store.conn.execute(
        f"SELECT {_CANON_COLS} FROM entity_canonical WHERE id = ?",
        [row[0]],
    ).fetchone()
    if not canon_row:
        return None
    aliases = _fetch_aliases(store, project_id, track, canon_row[0])
    return _row_to_canonical(canon_row, aliases)


def _by_lemma(
    store: DuckStore, lemma: str, project_id: str, track: str
) -> CanonicalEntity | None:
    if not lemma:
        return None
    rows = store.conn.execute(
        f"SELECT {_CANON_COLS} FROM entity_canonical "
        "WHERE project_id = ? AND track = ? AND canonical_name = ?",
        [project_id, track, lemma],
    ).fetchall()
    if not rows:
        return None
    canon = rows[0]
    aliases = _fetch_aliases(store, project_id, track, canon[0])
    return _row_to_canonical(canon, aliases)


def _by_fuzzy(
    store: DuckStore, lemma: str, project_id: str, track: str, max_distance: int = 2
) -> CanonicalEntity | None:
    if not lemma or len(lemma) < 3:
        return None
    rows = store.conn.execute(
        f"SELECT {_CANON_COLS} FROM entity_canonical "
        "WHERE project_id = ? AND track = ? AND mentions >= 1",
        [project_id, track],
    ).fetchall()
    best: tuple[int, tuple] | None = None
    for row in rows:
        dist = _levenshtein(lemma, row[3])
        if dist <= max_distance:
            if best is None or dist < best[0]:
                best = (dist, row)
    if best is None:
        return None
    canon = best[1]
    aliases = _fetch_aliases(store, project_id, track, canon[0])
    return _row_to_canonical(canon, aliases)


def _register_alias(
    store: DuckStore,
    alias: str,
    canonical_id: str,
    project_id: str,
    track: str,
) -> None:
    if not alias:
        return
    store.conn.execute(
        "INSERT INTO entity_alias (alias_surface, canonical_id, project_id, track, first_seen) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [alias, canonical_id, project_id, track, _ts()],
    )


def _increment_mentions(store: DuckStore, canonical_id: str, delta: int = 1) -> None:
    store.conn.execute(
        "UPDATE entity_canonical SET mentions = mentions + ? WHERE id = ?",
        [delta, canonical_id],
    )


def _lookup_existing_entity_node_id(store: DuckStore, text: str) -> str | None:
    """Sprint C D1 — ingest 경로가 `Node(kind='entity')` 먼저 저장한 뒤
    projection 에서 뒤늦게 linker 가 canonical 을 만드는 구조. 이름이 같은
    entity node 가 이미 있으면 그 id 로 연결해서 `node_id` NULL 을 방지."""
    if not text:
        return None
    try:
        row = store._read_conn().execute(
            "SELECT id FROM node WHERE kind='entity' AND text=? LIMIT 1",
            [text],
        ).fetchone()
    except Exception:
        return None
    return row[0] if row else None


def _create_canonical(
    store: DuckStore,
    canonical_name: str,
    kind: EntityKind,
    project_id: str,
    track: str,
    scope: str | None = None,
    *,
    surface: str | None = None,
) -> CanonicalEntity:
    """신규 canonical 생성. D1 근본 수정 (2026-04-24):

    ingest 는 `Node(kind='entity', text=surface)` 로 저장하지만 linker 는
    `canonical_name=lemma` 로 만들기 때문에 한글 조사 등으로 surface≠lemma
    인 경우 lookup 이 실패해 `node_id=NULL` 이 대량 발생했다. 이제:

    1) lemma(canonical_name) 로 조회
    2) (1) 실패 + surface 다르면 surface 로 재조회
    3) 그래도 없으면 NULL (드문 경우 — 백필 reconcile 로 처리)
    """
    cid = _ulid()
    node_id = _lookup_existing_entity_node_id(store, canonical_name)
    if node_id is None and surface and surface != canonical_name:
        node_id = _lookup_existing_entity_node_id(store, surface)
    # 병렬 호출 race + ULID 시계분해능 엣지케이스 방어
    with store.lock:
        store.conn.execute(
            f"INSERT INTO entity_canonical ({_CANON_COLS}, node_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            [cid, project_id, track, canonical_name, kind.value, scope, 1, node_id, _ts()],
        )
    return CanonicalEntity(
        id=cid,
        project_id=project_id,
        track=Track(track),
        canonical_name=canonical_name,
        kind=kind,
        scope=scope,
        mentions=1,
        aliases=[],
    )


def reconcile_canonical_node_ids(store: DuckStore) -> dict:
    """D1 근본 backfill (2026-04-24, CTAS 방식).

    9f7645e 의 UPDATE 방식은 DuckDB ART 인덱스 stale entry 를 trigger 하여
    FatalException + 서버 crash 를 유발. in-place UPDATE 대신 **새 테이블
    생성 → DROP/RENAME → 인덱스 재생성** 으로 ART 를 완전히 새로 빌드한다.

    채움 전략 (COALESCE):
      1) 기존 node_id 유지
      2) canonical_name 으로 node.text 매칭 (lemma)
      3) alias.alias_surface 로 node.text 매칭 (surface)
      4) 못 찾으면 NULL 유지

    반환: {"scanned": 전체, "matched_total": 채운 수, "still_null": 남은 NULL,
           "method": "ctas"}.
    """
    conn = store._read_conn()
    before_null = conn.execute(
        "SELECT COUNT(*) FROM entity_canonical WHERE node_id IS NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM entity_canonical").fetchone()[0]

    with store.lock:
        c = store.conn
        c.execute("BEGIN TRANSACTION")
        try:
            c.execute("DROP TABLE IF EXISTS entity_canonical_new")
            c.execute("""
                CREATE TABLE entity_canonical_new AS
                SELECT
                    ec.id,
                    ec.project_id,
                    ec.track,
                    ec.canonical_name,
                    ec.kind,
                    ec.scope,
                    ec.mentions,
                    COALESCE(
                        ec.node_id,
                        (SELECT n.id FROM node n
                           WHERE n.kind = 'entity' AND n.text = ec.canonical_name
                           LIMIT 1),
                        (SELECT n.id FROM node n
                           JOIN entity_alias ea ON
                                ea.canonical_id = ec.id
                            AND ea.project_id  = ec.project_id
                            AND ea.track       = ec.track
                           WHERE n.kind = 'entity' AND n.text = ea.alias_surface
                           LIMIT 1)
                    ) AS node_id,
                    ec.created_at
                FROM entity_canonical ec
            """)
            new_total = c.execute(
                "SELECT COUNT(*) FROM entity_canonical_new"
            ).fetchone()[0]
            if new_total != total:
                # 행 수 불일치 — abort. 원본 그대로 유지.
                raise RuntimeError(
                    f"CTAS row count mismatch: new={new_total} vs old={total}"
                )
            c.execute("DROP TABLE entity_canonical")
            c.execute("ALTER TABLE entity_canonical_new RENAME TO entity_canonical")
            # 인덱스 재생성 (migrations.sql 과 동일)
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_ent_project_track "
                "ON entity_canonical(project_id, track)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_ent_kind ON entity_canonical(kind)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_ent_node ON entity_canonical(node_id)"
            )
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise

    after_null = store._read_conn().execute(
        "SELECT COUNT(*) FROM entity_canonical WHERE node_id IS NULL"
    ).fetchone()[0]
    matched = before_null - after_null
    return {
        "scanned": before_null,
        "matched_total": matched,
        "still_null": after_null,
        "total": total,
        "method": "ctas",
    }


def link(
    surface: str,
    project_id: str,
    track: str | Track,
    store: DuckStore,
    *,
    kind_hint: EntityKind = EntityKind.OTHER,
    scope: str | None = None,
    fuzzy_max_distance: int = 2,
) -> LinkResult:
    """Surface form 을 canonical 로 링크. 기존 매칭 없으면 신규 생성."""
    surface = surface.strip()
    if not surface:
        raise ValueError("surface must be non-empty")

    t = (track if isinstance(track, str) else track.value)

    existing = _by_alias(store, surface, project_id, t)
    if existing is not None:
        _increment_mentions(store, existing.id)
        existing.mentions += 1
        return LinkResult(existing, "alias", is_new=False)

    lemma = _canonical_lemma(surface)
    existing = _by_lemma(store, lemma, project_id, t)
    if existing is not None:
        if surface != existing.canonical_name:
            _register_alias(store, surface, existing.id, project_id, t)
            existing.aliases.append(surface)
        _increment_mentions(store, existing.id)
        existing.mentions += 1
        return LinkResult(existing, "lemma", is_new=False)

    existing = _by_fuzzy(store, lemma, project_id, t, max_distance=fuzzy_max_distance)
    if existing is not None:
        _register_alias(store, surface, existing.id, project_id, t)
        existing.aliases.append(surface)
        _increment_mentions(store, existing.id)
        existing.mentions += 1
        return LinkResult(existing, "fuzzy", is_new=False)

    created = _create_canonical(
        store, lemma or surface, kind_hint, project_id, t, scope, surface=surface,
    )
    if surface != created.canonical_name:
        _register_alias(store, surface, created.id, project_id, t)
        created.aliases.append(surface)
    return LinkResult(created, "created", is_new=True)


def link_batch(
    surfaces: list[tuple[str, EntityKind]],
    project_id: str,
    track: str | Track,
    store: DuckStore,
) -> list[LinkResult]:
    out: list[LinkResult] = []
    for s, k in surfaces:
        try:
            out.append(link(s, project_id, track, store, kind_hint=k))
        except ValueError:
            continue
    return out


def canonical_by_id(store: DuckStore, canonical_id: str) -> CanonicalEntity | None:
    row = store.conn.execute(
        f"SELECT {_CANON_COLS} FROM entity_canonical WHERE id = ?",
        [canonical_id],
    ).fetchone()
    if not row:
        return None
    aliases = _fetch_aliases(store, row[1], row[2], row[0])
    return _row_to_canonical(row, aliases)


def list_canonicals(
    store: DuckStore,
    project_id: str,
    track: str | Track | None = None,
    kind: EntityKind | None = None,
) -> list[CanonicalEntity]:
    sql = f"SELECT {_CANON_COLS} FROM entity_canonical WHERE project_id = ?"
    params: list = [project_id]
    if track is not None:
        sql += " AND track = ?"
        params.append(track if isinstance(track, str) else track.value)
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind.value)
    sql += " ORDER BY mentions DESC, canonical_name"
    rows = store.conn.execute(sql, params).fetchall()
    return [
        _row_to_canonical(r, _fetch_aliases(store, r[1], r[2], r[0])) for r in rows
    ]


def stats(store: DuckStore, project_id: str, track: str | Track | None = None) -> dict:
    params: list = [project_id]
    sql = "SELECT kind, COUNT(*) FROM entity_canonical WHERE project_id = ?"
    if track is not None:
        sql += " AND track = ?"
        params.append(track if isinstance(track, str) else track.value)
    sql += " GROUP BY kind"
    kind_counts = dict(store.conn.execute(sql, params).fetchall())
    total = sum(kind_counts.values())
    alias_count = store.conn.execute(
        "SELECT COUNT(*) FROM entity_alias WHERE project_id = ?" + (
            " AND track = ?" if track is not None else ""
        ),
        params,
    ).fetchone()[0]
    return {
        "total_canonical": total,
        "by_kind": kind_counts,
        "total_aliases": alias_count,
    }
