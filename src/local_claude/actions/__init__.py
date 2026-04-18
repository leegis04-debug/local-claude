"""XML action 레지스트리 — plan 파일 §① 확장 Action 세트.

Phase 2: search_rag / search_graph / ask_deep / validate / critique
Phase 3: rerank / cross_check
Phase 4: run_test
"""

from .base import Action, ActionResult, REGISTRY, register
from . import (
    ask_deep,
    critique,
    cross_check,
    rerank,
    run_test,
    search_graph,
    search_rag,
    validate,
)

__all__ = [
    "Action",
    "ActionResult",
    "REGISTRY",
    "register",
    "ask_deep",
    "critique",
    "cross_check",
    "rerank",
    "run_test",
    "search_graph",
    "search_rag",
    "validate",
]
