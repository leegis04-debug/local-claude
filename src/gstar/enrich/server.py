"""4090/미니 PC 측 enrich SSE 엔드포인트.

가벼운 구현: FastAPI APIRouter 로 `/enrich/stream` 만 제공.
- 맥북에서 POST EnrichRequest (cluster_topic + under_connected)
- 서버에서 쿼리 생성 → web_search → Ollama synth → ndjson line 스트림 yield
- 맥북 클라이언트가 한 줄씩 EnrichItem 파싱

환경변수:
- `ENRICH_OLLAMA_HOST` — 기본 http://localhost:11434 (4090 에 직접 배포 시 localhost)
- `ENRICH_OLLAMA_MODEL` — 기본 gemma4:26b-a4b-it-q4_K_M
- `ENRICH_SEARCH_BACKEND` — "mock" | "brave" | "serpapi" | "firecrawl" (기본 mock)
- `ENRICH_MAX_RESULTS_PER_QUERY` — 기본 3
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from typing import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from gstar.enrich.policy import EnrichItem, generate_queries, hash_text
from gstar.enrich.synth import synthesize_from_result
from gstar.enrich.web_search import web_search
from gstar.stellar.gap_analysis import ClusterGap, NodeGap


class EnrichRequestModel(BaseModel):
    cluster_topic: str = ""
    under_connected: list[dict] = []
    stability_degree: int = 3
    max_facts_per_node: int = 3
    namespace: str = "web"
    project_id: str = ""
    request_id: str = ""


enrich_router = APIRouter(prefix="/enrich", tags=["enrich"])


def _reconstruct_gap(req: EnrichRequestModel) -> ClusterGap:
    under = []
    for d in req.under_connected:
        under.append(
            NodeGap(
                node_id=d.get("node_id", ""),
                text=d.get("text", ""),
                kind=d.get("kind", "fact"),
                current_degree=int(d.get("current_degree", 0)),
                shortfall=int(d.get("shortfall", 1)),
                neighbor_ids=list(d.get("neighbor_ids", [])),
            )
        )
    return ClusterGap(
        cluster_id="",
        cluster_topic=req.cluster_topic,
        stability_degree=req.stability_degree,
        member_count=len(under),
        internal_edges=0,
        ideal_internal_edges=0.0,
        density_ratio=0.0,
        under_connected=under,
        stable_ratio=0.0,
    )


@enrich_router.post("/stream")
async def enrich_stream(req: EnrichRequestModel):
    gap = _reconstruct_gap(req)

    async def generate() -> AsyncIterator[bytes]:
        queries = generate_queries(gap)
        max_per = req.max_facts_per_node
        backend = os.environ.get("ENRICH_SEARCH_BACKEND", "mock")
        for query, node_gap in queries:
            try:
                results = await web_search(query, backend=backend, top_k=max_per)
            except Exception:
                continue
            for r in results[:max_per]:
                try:
                    fact = await synthesize_from_result(
                        r, target_node_text=node_gap.text, target_node_kind=node_gap.kind
                    )
                except Exception:
                    continue
                if not fact or not fact.strip():
                    continue
                item = EnrichItem(
                    fact_text=fact.strip(),
                    fact_kind="fact",
                    source_url=r.get("url", ""),
                    query=query,
                    attach_to_node_ids=[node_gap.node_id],
                    edge_kind="evidence_of",
                    confidence=0.5,
                    content_hash=hash_text(fact),
                )
                yield (json.dumps(asdict(item), ensure_ascii=False) + "\n").encode("utf-8")
                await asyncio.sleep(0)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@enrich_router.get("/health")
def enrich_health() -> dict:
    return {
        "ok": True,
        "backend": os.environ.get("ENRICH_SEARCH_BACKEND", "ddg"),
        "ollama": os.environ.get("ENRICH_OLLAMA_HOST", "http://localhost:11434"),
        "model": os.environ.get("ENRICH_OLLAMA_MODEL", "gemma4:26b-a4b-it-q4_K_M"),
    }
