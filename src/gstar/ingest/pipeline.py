"""Ingest 파이프라인 조립.

단계:
1. chunk_path → facts
2. facts → fact Node 저장 + 임베딩 색인
3. entity candidates → entity Node 저장 + 임베딩 색인
4. entity ↔ fact → Edge (evidence_of)
5. entity ↔ entity → Edge (co_occurs)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from gstar.embedding import Embedder
from gstar.ingest.chunker import chunk_path, chunk_path_structured
from gstar.ingest.entity_extract import extract_candidates, extract_candidates_morph
from gstar.ingest.relation_infer import infer_relations, infer_relations_typed
from gstar.schema import Edge, Namespace, Node
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


@dataclass
class IngestReport:
    facts: int
    entities: int
    edges: int
    files_scanned: int


def ingest_path(
    target: Path,
    store: DuckStore,
    faiss: FaissStore,
    embedder: Embedder,
    *,
    root: Path | None = None,
    min_entity_count: int = 2,
    namespace: str | None = None,
    use_morph: bool = True,
    use_typed_relations: bool = True,
    track: str = "proposal",
    meta_index: "QdrantMetaIndex | None" = None,
) -> IngestReport:
    """원문 디렉터리 → fact/entity/edge 로 분해해 G 에 저장.

    `meta_index` (QdrantMetaIndex) 가 주어지면 각 fact 의 source 파일 경로로
    lookup 해 Qdrant payload 메타(tags/date/source/project/security_level/priority
    등)를 node.attrs 에 병합. Phase A2 의 "Qdrant metadata 주입" 경로.
    """
    import os as _os

    ns = namespace or store.active_namespace()
    known = {x.name for x in store.list_namespaces()}
    if ns not in known:
        store.upsert_namespace(
            Namespace(name=ns, description="(auto from ingest)", is_active=False)
        )

    # Phase H2: structured 모드 (document/section/fact 노드 + part_of edge)
    # env `GSTAR_STRUCTURED=on` (기본 off) — 기존 fact-only 경로와 호환.
    structured_enabled = _os.environ.get("GSTAR_STRUCTURED", "off").lower() in {
        "on", "1", "true"
    }
    doc_section_facts = None
    if structured_enabled:
        chunks_list = chunk_path_structured(target, root=root)
        facts = [f for sc in chunks_list for f in sc.facts]
        doc_section_facts = chunks_list
    else:
        facts = chunk_path(target, root=root)

    if not facts:
        return IngestReport(facts=0, entities=0, edges=0, files_scanned=0)

    fact_texts = [f.text for f in facts]
    fact_vecs = embedder.encode(fact_texts)

    # Phase A2: 같은 source 파일은 한 번만 lookup → 하위 fact 전체가 공유
    meta_cache: dict[str, dict] = {}

    def _file_meta(src: str) -> dict:
        if src in meta_cache:
            return meta_cache[src]
        m: dict = {}
        if meta_index is not None:
            try:
                found = meta_index.lookup(src)
                if found:
                    # qdrant_ids 는 fact 단위엔 불필요 (파일 단위만)
                    m = {k: v for k, v in found.items() if k != "qdrant_ids"}
            except Exception:
                m = {}
        meta_cache[src] = m
        return m

    # Phase H2: structured 모드 — document/section 노드 먼저 생성 (part_of edge 는 fact 저장 후)
    placeholder_to_node: dict[str, str] = {}   # chunker placeholder id → 실제 Node.id
    doc_section_count = 0
    if doc_section_facts is not None:
        for sc in doc_section_facts:
            doc_node = Node(
                kind="document",
                text=sc.document.title or sc.document.source,
                attrs={
                    "source": sc.document.source,
                    "content_hash": sc.document.content_hash,
                    **_file_meta(sc.document.source),
                },
                source_namespace=ns,
            )
            store.insert_node(doc_node)
            placeholder_to_node[sc.document.id] = doc_node.id
            doc_section_count += 1

            for sec in sc.sections:
                sec_node = Node(
                    kind="section",
                    text=sec.title,
                    attrs={
                        "source": sec.source,
                        "level": sec.level,
                        "line_no": sec.line_no,
                    },
                    source_namespace=ns,
                )
                store.insert_node(sec_node)
                placeholder_to_node[sec.id] = sec_node.id
                doc_section_count += 1

                # section part_of parent (document 또는 상위 section)
                parent_real = placeholder_to_node.get(sec.parent_id)
                if parent_real:
                    store.insert_edge(
                        Edge(
                            src=sec_node.id,
                            dst=parent_real,
                            kind="part_of",
                            weight=1.0,
                            evidence_ids=[],
                        )
                    )

    fact_nodes: list[Node] = []
    for f in facts:
        attrs: dict = {"source": f.source, "section": f.section, "line_no": f.line_no}
        attrs.update(_file_meta(f.source))  # Qdrant meta 우선 덮어씀 X — 후행 병합이라 덮어씀 O
        # 위에서 attrs 의 source/section/line_no 가 meta 에 의해 덮이면 안 됨 — 되돌림
        attrs["source"] = f.source
        attrs["section"] = f.section
        attrs["line_no"] = f.line_no
        n = Node(
            kind="fact",
            text=f.text,
            attrs=attrs,
            source_namespace=ns,
        )
        fact_nodes.append(n)
        store.insert_node(n)

        # Phase H3: event/evidence 정규식 추출 (structured 모드만)
        if doc_section_facts is not None:
            try:
                from gstar.ingest.event_extract import (
                    event_text,
                    extract_events_from_text,
                    extract_evidence_from_text,
                )
                for ev in extract_events_from_text(f.text, line_no=f.line_no):
                    ev_node = Node(
                        kind="event",
                        text=event_text(ev),
                        attrs={
                            "year": ev.year,
                            "month": ev.month,
                            "day": ev.day,
                            "raw": ev.date_text,
                            "source": f.source,
                            "line_no": ev.line_no,
                        },
                        source_namespace=ns,
                    )
                    store.insert_node(ev_node)
                    # (event) -[when_of]-> (fact)
                    store.insert_edge(Edge(
                        src=ev_node.id, dst=n.id, kind="when_of", weight=1.0, evidence_ids=[],
                    ))
                for evd in extract_evidence_from_text(f.text, line_no=f.line_no):
                    evd_node = Node(
                        kind="evidence",
                        text=evd.text[:500],
                        attrs={
                            "kind": evd.kind,
                            "source": f.source,
                            "line_no": evd.line_no,
                        },
                        source_namespace=ns,
                    )
                    store.insert_node(evd_node)
                    # (fact) -[evidence_of]-> (evidence)
                    store.insert_edge(Edge(
                        src=n.id, dst=evd_node.id, kind="evidence_of", weight=1.0, evidence_ids=[],
                    ))
            except Exception:
                pass    # H3 추출 실패가 ingest 전체를 막지 않음

        # Phase H2: fact part_of section (structured 모드만)
        if doc_section_facts is not None:
            sec_real = placeholder_to_node.get(getattr(f, "section_id", ""))
            if sec_real:
                store.insert_edge(
                    Edge(
                        src=n.id,
                        dst=sec_real,
                        kind="part_of",
                        weight=1.0,
                        evidence_ids=[],
                    )
                )

    faiss.add_batch([n.id for n in fact_nodes], fact_vecs)

    id_fact_pairs = list(zip([n.id for n in fact_nodes], facts))
    if use_morph:
        candidates = extract_candidates_morph(id_fact_pairs, min_count=min_entity_count)
    else:
        candidates = extract_candidates(id_fact_pairs, min_count=min_entity_count)

    entity_id_by_name: dict[str, str] = {}
    entity_fact_map: dict[str, set[str]] = {}
    entity_types: dict[str, str] = {}
    if candidates:
        ent_texts = [c.name for c in candidates]
        ent_vecs = embedder.encode(ent_texts)
        ent_ids: list[str] = []
        try:
            from gstar.entity.classifier import classify as _classify
        except ImportError:
            _classify = None

        for c in candidates:
            n = Node(
                kind="entity",
                text=c.name,
                attrs={"mentions": c.mentions},
                source_namespace=ns,
            )
            store.insert_node(n)
            entity_id_by_name[c.name] = n.id
            entity_fact_map[n.id] = set(c.fact_ids)
            ent_ids.append(n.id)
            if _classify is not None:
                try:
                    kind = _classify(c.name, context="", track=track)
                    entity_types[n.id] = kind.value
                except Exception:
                    pass
        faiss.add_batch(ent_ids, ent_vecs)

    if use_typed_relations and entity_types:
        fact_texts_by_id = {nid: f.text for nid, f in zip([n.id for n in fact_nodes], facts)}
        inferred = infer_relations_typed(entity_fact_map, entity_types, fact_texts_by_id)
    else:
        inferred = infer_relations(entity_fact_map)

    for ie in inferred:
        edge = Edge(
            src=ie.src,
            dst=ie.dst,
            kind=ie.kind,
            weight=ie.weight,
            evidence_ids=ie.evidence_ids,
        )
        store.insert_edge(edge)
        rt = getattr(ie, "relation_type", None) or ie.kind
        if rt:
            store.conn.execute(
                "UPDATE edge SET relation_type = ? WHERE id = ?",
                [rt, edge.id],
            )

    files_scanned = len({f.source for f in facts})

    return IngestReport(
        facts=len(fact_nodes),
        entities=len(candidates),
        edges=len(inferred),
        files_scanned=files_scanned,
    )
