"""단계별 retrieval/summarize/generate 전략 매트릭스.

`TaskDefinition.stage_role(stage)` 가 StageRole 반환. 트랙 공용 기본값 +
트랙에서 오버라이드.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


SummaryDepth = Literal["deep", "light", "none"]


@dataclass
class StageRole:
    retrieval_query: str                     # .format(input=..., project_goal=...)
    retrieval_top_k: int
    entity_relation_types: list[str]
    summary_depth: SummaryDepth
    section_schema: str                      # template.py 의 스키마 이름
    coherence_retries: int
    ollama_model: str = os.environ.get("PROJECTOR_MODEL", "qwen2.5-14b")
    system_prompt: str = ""
    max_prompt_chars: int = 4500
    required_fact_kinds: list[str] = field(default_factory=list)


DEFAULT_ROLE = StageRole(
    retrieval_query="{input}",
    retrieval_top_k=5,
    entity_relation_types=["co_occurs", "evidence_of"],
    summary_depth="light",
    section_schema="proposal_8",
    coherence_retries=1,
)


PROPOSAL_ROLES: dict[str, StageRole] = {
    "idea": StageRole(
        retrieval_query="{input} 선행사례 유사 과제",
        retrieval_top_k=3,
        entity_relation_types=["co_occurs"],
        summary_depth="light",
        section_schema="idea_3",
        coherence_retries=1,
        system_prompt="당신은 정부 R&D 사업계획서 작성 전문가다. 간결하고 구체적으로.",
    ),
    "debate": StageRole(
        retrieval_query="{input} 찬반 반론",
        retrieval_top_k=4,
        entity_relation_types=["co_occurs"],
        summary_depth="light",
        section_schema="debate_4",
        coherence_retries=1,
        system_prompt="당신은 비판적 reviewer 이자 제안자다. 양측 관점을 대등하게.",
    ),
    "structure": StageRole(
        retrieval_query="{input} 구조 체계 방법론",
        retrieval_top_k=6,
        entity_relation_types=["co_occurs", "participates_in"],
        summary_depth="deep",
        section_schema="structure_7",
        coherence_retries=1,
        system_prompt="논리 구조를 명확히. 문제→가설→방법→검증.",
    ),
    "spec": StageRole(
        retrieval_query="{input} 기술명세 API 아키텍처",
        retrieval_top_k=8,
        entity_relation_types=["measures", "depends_on"],
        summary_depth="deep",
        section_schema="spec_8",
        coherence_retries=2,
        required_fact_kinds=["metric"],
        system_prompt="기술 구현을 정확하고 구체적으로. 모호한 표현 금지.",
    ),
    "risk-check": StageRole(
        retrieval_query="{input} 리스크 위험 실패사례",
        retrieval_top_k=5,
        entity_relation_types=["co_occurs"],
        summary_depth="light",
        section_schema="idea_3",
        coherence_retries=1,
        system_prompt="리스크 식별자. 과장·모호성·차별성 부재를 공격적으로 점검.",
    ),
    "experiment-plan": StageRole(
        retrieval_query="{input} 실험 프로토콜 검증",
        retrieval_top_k=6,
        entity_relation_types=["uses", "yields"],
        summary_depth="deep",
        section_schema="structure_7",
        coherence_retries=2,
        system_prompt="실험 설계자. 재현 가능한 프로토콜을 구체적으로.",
    ),
    "proposal": StageRole(
        retrieval_query="{input} 추진체계 예산 확산",
        retrieval_top_k=10,
        entity_relation_types=["measures", "participates_in", "budgets_for", "schedules"],
        summary_depth="deep",
        section_schema="proposal_8",
        coherence_retries=2,
        required_fact_kinds=["metric", "decision"],
        system_prompt="정부 R&D 사업계획서 최종본. 심사위원 관점에서 설득력·정량성·차별성.",
    ),
    "final-doc": StageRole(
        retrieval_query="{input} 결과 성과 기여",
        retrieval_top_k=10,
        entity_relation_types=["measures", "yields"],
        summary_depth="deep",
        section_schema="final_doc_8",
        coherence_retries=2,
        system_prompt="최종 결과 보고서. Executive Summary 부터 강렬하게.",
    ),
}


RESEARCH_ROLES: dict[str, StageRole] = {
    **PROPOSAL_ROLES,
    "proposal": StageRole(
        retrieval_query="{input} 연구 방법 가설 실험",
        retrieval_top_k=10,
        entity_relation_types=["tests", "uses", "yields", "measures"],
        summary_depth="deep",
        section_schema="research_9",
        coherence_retries=2,
        required_fact_kinds=["metric", "decision"],
        system_prompt="연구 과학자. 가설·방법·실험·결과 체인 완전성 필수.",
    ),
    "lab-note": StageRole(
        retrieval_query="{input} 실험 기록",
        retrieval_top_k=4,
        entity_relation_types=["uses", "yields"],
        summary_depth="light",
        section_schema="research_9",
        coherence_retries=1,
        system_prompt="실험 노트 기록자. 재현 가능한 상세 기록.",
    ),
}


CODING_ROLES: dict[str, StageRole] = {
    "explore": StageRole(
        retrieval_query="{input} 기존 구현 유사 패턴",
        retrieval_top_k=8,
        entity_relation_types=["co_occurs", "defines", "imports"],
        summary_depth="light",
        section_schema="explore",
        coherence_retries=0,
        system_prompt="senior engineer. 기존 코드를 먼저 파악하고 패턴을 따른다.",
    ),
    "plan": StageRole(
        retrieval_query="{input} 아키텍처 시그니처",
        retrieval_top_k=6,
        entity_relation_types=["defines", "calls", "depends_on"],
        summary_depth="deep",
        section_schema="plan",
        coherence_retries=1,
        system_prompt="architect. 변경 파일·시그니처·의존성을 표로 정리.",
    ),
    "implement": StageRole(
        retrieval_query="{input}",
        retrieval_top_k=4,
        entity_relation_types=["defines", "calls"],
        summary_depth="deep",
        section_schema="implement",
        coherence_retries=2,
        ollama_model="gemma4:26b-a4b-it-q4_K_M",
        system_prompt=(
            "senior engineer. 반드시 plan 의 시그니처와 일치시키고 기존 import 관례를 따른다. "
            "코드만 출력, 설명 최소."
        ),
        max_prompt_chars=6000,
    ),
    "test": StageRole(
        retrieval_query="{input} 테스트 패턴",
        retrieval_top_k=4,
        entity_relation_types=["tested_by", "defines"],
        summary_depth="light",
        section_schema="test",
        coherence_retries=1,
        system_prompt="test engineer. 경계조건·실패케이스·edge case 포함.",
    ),
    "review": StageRole(
        retrieval_query="{input}",
        retrieval_top_k=3,
        entity_relation_types=["calls", "imports", "tested_by"],
        summary_depth="deep",
        section_schema="review",
        coherence_retries=2,
        system_prompt="reviewer. 타입·import·커버리지 3축으로 검사.",
    ),
}


DOCUMENT_ROLES: dict[str, StageRole] = {
    "outline": StageRole(
        retrieval_query="{input} 관련 자료",
        retrieval_top_k=5,
        entity_relation_types=["cites"],
        summary_depth="light",
        section_schema="outline",
        coherence_retries=0,
        system_prompt="writer. 독자·목적 먼저, 섹션 구성 다음.",
    ),
    "draft": StageRole(
        retrieval_query="{input}",
        retrieval_top_k=6,
        entity_relation_types=["cites", "supports"],
        summary_depth="deep",
        section_schema="draft",
        coherence_retries=1,
        system_prompt="writer. outline 따라 섹션별 drafting. 반복 최소.",
        max_prompt_chars=5500,
    ),
    "revise": StageRole(
        retrieval_query="{input}",
        retrieval_top_k=4,
        entity_relation_types=["cites", "supports"],
        summary_depth="deep",
        section_schema="revise",
        coherence_retries=1,
        system_prompt="editor. 어색한 표현·논리 점프·인용 누락 체크.",
    ),
    "finalize": StageRole(
        retrieval_query="{input}",
        retrieval_top_k=3,
        entity_relation_types=["cites"],
        summary_depth="light",
        section_schema="finalize",
        coherence_retries=1,
        system_prompt="editor. 포맷·제목·abstract 정돈.",
    ),
}
