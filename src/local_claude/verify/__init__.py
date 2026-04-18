"""Phase 3 — 검증 계층 (리랭커 + hallucination detector).

외부 ML 모델 의존성 없이 동작한다. LLM 모드가 필요하면
기존 `ask-gemma` 서브프로세스를 재사용한다.

주의: `cross_check`/`detector` 는 **모듈 이름과 함수 이름이 겹치기 때문에**
함수는 `check_claim` / `detect_hallucinations` 별칭으로만 re-export 한다.
직접 함수가 필요하면 `from local_claude.verify.cross_check import cross_check`
같이 서브모듈에서 가져올 것.
"""

from . import claims, cross_check, detector, reranker
from .cross_check import CrossCheckResult
from .cross_check import cross_check as check_claim
from .detector import HallucinationReport
from .detector import detect as detect_hallucinations
from .reranker import (
    Document,
    HeuristicReranker,
    LLMReranker,
    NoopReranker,
    RankedDocument,
    Reranker,
    get_reranker,
)

__all__ = [
    "CrossCheckResult",
    "Document",
    "HallucinationReport",
    "HeuristicReranker",
    "LLMReranker",
    "NoopReranker",
    "RankedDocument",
    "Reranker",
    "check_claim",
    "claims",
    "cross_check",
    "detect_hallucinations",
    "detector",
    "get_reranker",
    "reranker",
]
