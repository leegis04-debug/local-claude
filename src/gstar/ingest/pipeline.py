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
from gstar.ingest.chunker import chunk_path
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
) -> IngestReport:
    ns = namespace or store.active_namespace()
    known = {x.name for x in store.list_namespaces()}
    if ns not in known:
        store.upsert_namespace(
            Namespace(name=ns, description="(auto from ingest)", is_active=False)
        )

    facts = chunk_path(target, root=root)
    if not facts:
        return IngestReport(facts=0, entities=0, edges=0, files_scanned=0)

    fact_texts = [f.text for f in facts]
    fact_vecs = embedder.encode(fact_texts)

    fact_nodes: list[Node] = []
    for f in facts:
        n = Node(
            kind="fact",
            text=f.text,
            attrs={"source": f.source, "section": f.section, "line_no": f.line_no},
            source_namespace=ns,
        )
        fact_nodes.append(n)
        store.insert_node(n)

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
