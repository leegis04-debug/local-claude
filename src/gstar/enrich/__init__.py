"""Gap-driven web enrichment.

철학: G 의 "3 연결 안정" 원칙을 enrich 트리거로 활용.
- 맥북 selection loop 가 매 cycle 후 cluster gap 측정
- degree < 3 노드가 있으면 4090 에 비차단 스트림 enrich 요청
- 4090 이 web search + Ollama 요약 → fact + edge pair 를 stream yield
- 맥북 큐 drain 시 fact insert + 기존 under-connected 노드와 edge 자동 생성
- 노드 degree 가 3→4 넘으면 기존 `emergence_event` 가 자동 트리거

모듈:
- `policy.py` — gap → query 생성, dedup, 쿼터 정책
- `client.py` — 맥북 측 EnrichSubscriber (SSE 구독, 큐 적재)
- `server.py` — 4090/미니 PC 측 /enrich/stream FastAPI 엔드포인트
- `web_search.py` — Brave/SerpAPI/Firecrawl 어댑터
- `synth.py` — 검색 결과 → Fact + Edge 변환 (Ollama)
"""

from gstar.enrich.policy import EnrichRequest, EnrichItem, generate_queries
from gstar.enrich.client import EnrichSubscriber, MockEnrichStream
from gstar.enrich.server import enrich_router

__all__ = [
    "EnrichRequest",
    "EnrichItem",
    "generate_queries",
    "EnrichSubscriber",
    "MockEnrichStream",
    "enrich_router",
]
