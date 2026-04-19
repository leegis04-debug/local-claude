"""Neo4j 그래프 dump → G entity_canonical + edge 매핑 — Phase A3.

Neo4j 에 저장된 수작업 지식 그래프 (Paper/Method/Project/CodeRepo + APPLIES_METHOD 등) 를
G 의 entity 모델로 이관. 기존 ingest_path 가 다루지 못한 "상위 메타-레벨" 지식.

원본 스키마(2026-04-19 기준):
  라벨  : Paper(47), Method(135), Project(21), CodeRepo(16)
  관계  : APPLIES_METHOD(Project→Method), USES_METHOD(Paper→Method),
          MODIFIES(Project→CodeRepo), IMPROVES(Method→Method), CITES(Paper→Paper)

매핑:
  Paper     → EntityKind.CITATION   text=title
  Method    → EntityKind.METHOD     text=name
  Project   → EntityKind.PROJECT    text=slug
  CodeRepo  → EntityKind.MODULE     text=path (basename 사용)
  others    → EntityKind.OTHER      text=label-derived

  APPLIES_METHOD → RelationType.USES
  USES_METHOD    → RelationType.USES
  MODIFIES       → RelationType.DEFINES
  IMPROVES       → RelationType.DEPENDS_ON
  CITES          → RelationType.CITES
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


# --- Neo4j label → (EntityKind, track) --------------------------------------

# 주의: EntityKind/Track 은 런타임 임포트 (pydantic import 피함)
_LABEL_MAP: dict[str, tuple[str, str]] = {
    # (kind.value, track)
    "Paper":    ("citation", "research"),
    "Method":   ("method",   "research"),
    "Project":  ("project",  "coding"),
    "CodeRepo": ("module",   "coding"),
    "Dataset":  ("dataset",  "research"),
    "Metric":   ("metric",   "proposal"),
    "FormSection":   ("document", "document"),
    "PromptVersion": ("document", "document"),
}

# Neo4j 관계 → G relation_type 문자열
_REL_MAP: dict[str, str] = {
    "APPLIES_METHOD": "uses",
    "USES_METHOD":    "uses",
    "MODIFIES":       "defines",
    "IMPROVES":       "depends_on",
    "CITES":          "cites",
}


@dataclass
class MappedEntity:
    neo4j_id: int
    label: str
    canonical_name: str
    kind: str          # EntityKind.value
    track: str
    project_id: str
    attrs: dict[str, Any]


@dataclass
class MappedRelation:
    src_neo4j: int
    dst_neo4j: int
    rel: str
    relation_type: str  # RelationType.value
    attrs: dict[str, Any]


def _derive_name(label: str, props: dict[str, Any]) -> str:
    """라벨 종류에 맞춘 canonical_name 유추."""
    if label == "Paper":
        t = (props.get("title") or "").strip()
        return t or f"paper-{props.get('_nid', 'unknown')}"
    if label == "Method":
        return (props.get("name") or props.get("canonical_name") or "").strip() or f"method-{props.get('_nid','')}"
    if label == "Project":
        return (props.get("slug") or props.get("name") or "").strip() or f"project-{props.get('_nid','')}"
    if label == "CodeRepo":
        path = (props.get("path") or "").strip()
        if path:
            return path.rstrip("/").rsplit("/", 1)[-1] or path
        return f"coderepo-{props.get('_nid','')}"
    # 기타
    return (props.get("name") or props.get("slug") or props.get("title") or label).strip() or f"{label.lower()}-{props.get('_nid','')}"


def _derive_project_id(label: str, props: dict[str, Any]) -> str:
    """entity_canonical.project_id 를 결정.

    Project 자체는 slug 가 project_id, CodeRepo 는 path 추정,
    나머지는 label 기반 공통 bucket.
    """
    if label == "Project":
        slug = (props.get("slug") or "").strip()
        return slug or "neo4j_project"
    if label == "CodeRepo":
        path = (props.get("path") or "").strip().lstrip("/")
        # /code_repos/DagyeomEyekit-v2-CameraServer-main → DagyeomEyekit-v2-CameraServer-main
        if path.startswith("code_repos/"):
            path = path[len("code_repos/"):]
        return path.split("/", 1)[0] or "neo4j_coderepo"
    if label == "Paper":
        return "neo4j_papers"
    if label == "Method":
        return "neo4j_methods"
    return f"neo4j_{label.lower()}"


def map_node(row: dict[str, Any]) -> MappedEntity | None:
    """cypher dump row → MappedEntity.

    row 스키마: {nid: int, label: str, props: dict}
    """
    label = row.get("label")
    if not label or label not in _LABEL_MAP:
        return None
    kind, track = _LABEL_MAP[label]
    props = dict(row.get("props") or {})
    props.pop("embedding", None)  # 큰 벡터 제거
    props["_nid"] = row.get("nid")
    name = _derive_name(label, props)
    project_id = _derive_project_id(label, props)
    # attrs: neo4j 참조 + 주요 필드
    attrs: dict[str, Any] = {"neo4j_id": row.get("nid"), "neo4j_label": label}
    for k in ("year", "authors", "venue", "abstract", "category", "stage",
              "language", "framework", "path"):
        v = props.get(k)
        if v not in (None, "", []):
            attrs[k] = v
    return MappedEntity(
        neo4j_id=int(row["nid"]),
        label=label,
        canonical_name=name,
        kind=kind,
        track=track,
        project_id=project_id,
        attrs=attrs,
    )


def map_relation(row: dict[str, Any]) -> MappedRelation | None:
    """cypher dump row → MappedRelation.

    row 스키마: {src: int, dst: int, rel: str, props: dict}
    """
    rel = row.get("rel")
    if not rel:
        return None
    rt = _REL_MAP.get(rel, rel.lower())
    return MappedRelation(
        src_neo4j=int(row["src"]),
        dst_neo4j=int(row["dst"]),
        rel=rel,
        relation_type=rt,
        attrs=dict(row.get("props") or {}),
    )


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# --- apply ------------------------------------------------------------------

def apply_dump(
    nodes_path: Path,
    edges_path: Path,
    store,               # DuckStore
    embedder,            # Embedder — entity 텍스트 임베딩용
    faiss,               # FaissStore
    namespace: str = "graph_import",
) -> dict[str, int]:
    """nodes/edges jsonl 을 DuckStore 에 upsert.

    멱등성: entity_canonical 에 (project_id, track, canonical_name) 중복이면 skip.
    Node('entity') 는 neo4j_id attr 기준 중복 검사.
    edge 는 src_node_id + dst_node_id + kind 조합 기준.
    """
    from gstar.entity.types import EntityKind, Track
    from gstar.schema import Edge as GEdge, Namespace, Node as GNode

    ts_now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    from ulid import ULID

    # namespace 보장
    known = {x.name for x in store.list_namespaces()}
    if namespace not in known:
        store.upsert_namespace(
            Namespace(name=namespace, description="(auto from neo4j import)", is_active=False)
        )

    # neo4j_id → G node_id 매핑
    nid_to_node: dict[int, str] = {}
    created_nodes = 0
    reused_nodes = 0
    new_entities = 0
    reused_entities = 0
    texts_to_embed: list[str] = []
    ids_to_embed: list[str] = []

    # 기존 graph_import 노드 재사용 (멱등)
    with store.lock:
        existing_nodes = store.conn.execute(
            "SELECT id, attrs_json FROM node WHERE kind='entity' AND source_namespace=?",
            [namespace],
        ).fetchall()
    for row in existing_nodes:
        try:
            a = json.loads(row[1])
            nid = a.get("neo4j_id")
            if nid is not None:
                nid_to_node[int(nid)] = row[0]
        except Exception:
            continue

    mapped_entities: list[MappedEntity] = []
    for row in iter_jsonl(nodes_path):
        me = map_node(row)
        if me is None:
            continue
        mapped_entities.append(me)

    for me in mapped_entities:
        if me.neo4j_id in nid_to_node:
            reused_nodes += 1
        else:
            node = GNode(
                kind="entity",
                text=me.canonical_name,
                attrs=me.attrs,
                source_namespace=namespace,
            )
            store.insert_node(node)
            nid_to_node[me.neo4j_id] = node.id
            created_nodes += 1
            texts_to_embed.append(me.canonical_name)
            ids_to_embed.append(node.id)

        # entity_canonical upsert (project_id + track + canonical_name 기준)
        # DuckDB threadpool 병렬 호출 race 방어: lock 내부에서 check + insert.
        # PK 는 ULID 라 충돌 가능성 극히 낮지만, 동일 프로세스 내 시계 분해능
        # 이슈·재시도 경로까지 덮기 위해 ON CONFLICT DO NOTHING 적용.
        with store.lock:
            existing = store.conn.execute(
                "SELECT id FROM entity_canonical WHERE project_id=? AND track=? AND canonical_name=?",
                [me.project_id, me.track, me.canonical_name],
            ).fetchone()
            if existing:
                reused_entities += 1
                continue
            cid = str(ULID())
            store.conn.execute(
                "INSERT INTO entity_canonical "
                "(id, project_id, track, canonical_name, kind, scope, mentions, node_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?) ON CONFLICT DO NOTHING",
                [cid, me.project_id, me.track, me.canonical_name, me.kind,
                 1, nid_to_node[me.neo4j_id], ts_now],
            )
        new_entities += 1

    # embed 일괄
    if texts_to_embed and embedder is not None and faiss is not None:
        try:
            vecs = embedder.encode(texts_to_embed)
            faiss.add_batch(ids_to_embed, vecs)
        except Exception:
            pass  # embed 실패해도 entity 자체는 유지

    # edges
    created_edges = 0
    skipped_edges = 0
    for row in iter_jsonl(edges_path):
        mr = map_relation(row)
        if mr is None:
            continue
        src_id = nid_to_node.get(mr.src_neo4j)
        dst_id = nid_to_node.get(mr.dst_neo4j)
        if not src_id or not dst_id:
            skipped_edges += 1
            continue
        # 중복 edge 체크 (src, dst, relation_type)
        with store.lock:
            existing = store.conn.execute(
                "SELECT id FROM edge WHERE src=? AND dst=? AND relation_type=?",
                [src_id, dst_id, mr.relation_type],
            ).fetchone()
        if existing:
            skipped_edges += 1
            continue
        edge = GEdge(
            src=src_id,
            dst=dst_id,
            kind=mr.relation_type,
            weight=1.0,
            evidence_ids=[],
        )
        store.insert_edge(edge)
        with store.lock:
            store.conn.execute(
                "UPDATE edge SET relation_type = ? WHERE id = ?",
                [mr.relation_type, edge.id],
            )
        created_edges += 1

    try:
        faiss.save()
    except Exception:
        pass

    return {
        "nodes_created": created_nodes,
        "nodes_reused": reused_nodes,
        "entities_new": new_entities,
        "entities_reused": reused_entities,
        "edges_created": created_edges,
        "edges_skipped": skipped_edges,
        "namespace": namespace,
    }
