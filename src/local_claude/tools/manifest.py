"""각 action 의 입력 스키마·설명·예시 명세 (connect-ai manifest).

JSON Schema 의 최소 부분집합만 사용 — connect-ai 가 UI 폼·검증에 쓰기 좋은 형태.
실제 검증은 action 핸들러가 내부에서 수행 (여기서는 힌트만).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..actions.base import REGISTRY


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    examples: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "examples": self.examples,
        }


def _string(desc: str = "", required: bool = False) -> dict[str, Any]:
    return {"type": "string", "description": desc, "required": required}


def _int(desc: str = "", required: bool = False) -> dict[str, Any]:
    return {"type": "integer", "description": desc, "required": required}


def _array(desc: str = "", required: bool = False, items: dict | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "array", "description": desc, "required": required}
    if items is not None:
        out["items"] = items
    return out


MANIFEST: dict[str, ToolSpec] = {
    "search_rag": ToolSpec(
        name="search_rag",
        description="gateway /search/hybrid 로 RAG 검색. 기본적으로 govsupport 소스 제외.",
        input_schema={
            "query": _string("자연어 검색어", required=True),
            "top_k": _int("상위 k 결과 (기본 5)"),
            "exclude_sources": _array("제외 소스 목록 (기본 ['govsupport'])", items={"type": "string"}),
            "sources": _array("명시 소스만 조회", items={"type": "string"}),
        },
        examples=[
            {"query": "미트앤퓨쳐 비전 AI", "top_k": 5},
            {"query": "GraphRAG 설계", "exclude_sources": []},
        ],
    ),
    "search_graph": ToolSpec(
        name="search_graph",
        description="Neo4j cypher 읽기 쿼리 (파괴적 쿼리 차단).",
        input_schema={
            "cypher": _string("읽기 전용 Cypher", required=True),
            "params": {"type": "object", "description": "바인드 파라미터"},
        },
        examples=[{"cypher": "MATCH (n:Method) RETURN n LIMIT 5"}],
    ),
    "ask_deep": ToolSpec(
        name="ask_deep",
        description="4090 a4b 에 장문 추론 위임 (ask-gemma --deep).",
        input_schema={
            "prompt": _string("추론할 프롬프트", required=True),
            "rag": {"type": "boolean", "description": "RAG 근거 주입 여부"},
            "timeout": _int("초 단위 (기본 120)"),
        },
        examples=[{"prompt": "이 아키텍처의 실패 가능 지점은?", "rag": True}],
    ),
    "validate": ToolSpec(
        name="validate",
        description="MCP jw-validator 프록시 (/mcp/jw/{tool}).",
        input_schema={
            "tool": _string("MCP 도구명 (기본 validate_step)"),
            "args": {"type": "object", "description": "도구 인자"},
        },
        examples=[{"tool": "validate_step", "args": {"step": "idea", "content": "..."}}],
    ),
    "critique": ToolSpec(
        name="critique",
        description="MCP doc-thinking 프록시 (/mcp/doc/{tool}).",
        input_schema={
            "tool": _string("MCP 도구명 (기본 critique_thought)"),
            "args": {"type": "object", "description": "도구 인자"},
        },
        examples=[{"tool": "critique_thought", "args": {"thought": "..."}}],
    ),
    "rerank": ToolSpec(
        name="rerank",
        description="근거 목록을 쿼리 관련도로 재정렬 (noop/heuristic/llm).",
        input_schema={
            "query": _string("쿼리", required=True),
            "docs": _array("문서 객체 배열", required=True, items={"type": "object"}),
            "mode": _string("noop | heuristic(기본) | llm"),
            "top_k": _int("상위 k"),
            "alpha": {"type": "number", "description": "heuristic 가중치 0-1"},
        },
        examples=[{"query": "RAG 리랭커", "docs": [{"text": "..."}]}],
    ),
    "cross_check": ToolSpec(
        name="cross_check",
        description="답변 claim 들과 RAG 근거의 교차 검증 (hallucination 탐지).",
        input_schema={
            "answer": _string("검증할 답변", required=True),
            "evidences": _array("근거 객체 배열", required=True, items={"type": "object"}),
            "mode": _string("local | llm"),
        },
        examples=[{"answer": "Gateway 는 X-Auth-Token 사용", "evidences": [{"text": "..."}]}],
    ),
    "run_test": ToolSpec(
        name="run_test",
        description="프로젝트 테스트 자동 감지 실행 + 재시도.",
        input_schema={
            "cmd": _string("명시적 명령 override"),
            "retries": _int("실패 시 재시도 횟수"),
            "timeout": _int("초"),
            "cwd": _string("프로젝트 루트"),
        },
        examples=[{}, {"cmd": "pytest tests/unit", "retries": 1}],
    ),
}


def list_tools() -> list[dict[str, Any]]:
    """REGISTRY 와 MANIFEST 교집합 기준으로 반환 — 실제 등록된 action 만."""
    names = sorted(set(REGISTRY.keys()) & set(MANIFEST.keys()))
    return [MANIFEST[n].to_dict() for n in names]


def get(name: str) -> ToolSpec | None:
    return MANIFEST.get(name)
