"""엔티티/관계 타입 정의.

트랙별 엔티티 종류 + typed relations. `StrEnum` 으로 DuckDB 저장 시 문자열화.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Track(StrEnum):
    """P축 투영 트랙. Phase B `TaskDefinition.name` 과 일치."""

    PROPOSAL = "proposal"
    RESEARCH = "research"
    CODING = "coding"
    DOCUMENT = "document"


class EntityKind(StrEnum):
    PERSON = "person"
    ORG = "org"
    DOCUMENT = "document"
    TIMELINE = "timeline"
    OTHER = "other"

    PROJECT = "project"
    METRIC = "metric"
    BUDGET = "budget"
    TECHNOLOGY = "technology"

    HYPOTHESIS = "hypothesis"
    METHOD = "method"
    DATASET = "dataset"
    EXPERIMENT = "experiment"
    RESULT = "result"

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    VARIABLE = "variable"
    API_ENDPOINT = "api_endpoint"
    TEST_CASE = "test_case"

    CITATION = "citation"
    CLAIM = "claim"


class RelationType(StrEnum):
    CO_OCCURS = "co_occurs"
    EVIDENCE_OF = "evidence_of"

    MEASURES = "measures"
    PARTICIPATES_IN = "participates_in"
    DEPENDS_ON = "depends_on"
    BUDGETS_FOR = "budgets_for"
    SCHEDULES = "schedules"

    TESTS = "tests"
    USES = "uses"
    YIELDS = "yields"

    CALLS = "calls"
    IMPORTS = "imports"
    TESTED_BY = "tested_by"
    DEFINES = "defines"

    CITES = "cites"
    SUPPORTS = "supports"


RELATION_APPLIES_TO: dict[RelationType, tuple[Track, ...]] = {
    RelationType.CO_OCCURS: tuple(Track),
    RelationType.EVIDENCE_OF: tuple(Track),
    RelationType.MEASURES: (Track.PROPOSAL, Track.RESEARCH),
    RelationType.PARTICIPATES_IN: (Track.PROPOSAL, Track.RESEARCH),
    RelationType.DEPENDS_ON: (Track.PROPOSAL, Track.RESEARCH, Track.CODING),
    RelationType.BUDGETS_FOR: (Track.PROPOSAL,),
    RelationType.SCHEDULES: (Track.PROPOSAL, Track.RESEARCH),
    RelationType.TESTS: (Track.RESEARCH,),
    RelationType.USES: (Track.RESEARCH, Track.CODING),
    RelationType.YIELDS: (Track.RESEARCH,),
    RelationType.CALLS: (Track.CODING,),
    RelationType.IMPORTS: (Track.CODING,),
    RelationType.TESTED_BY: (Track.CODING,),
    RelationType.DEFINES: (Track.CODING,),
    RelationType.CITES: (Track.DOCUMENT, Track.RESEARCH),
    RelationType.SUPPORTS: (Track.DOCUMENT, Track.RESEARCH),
}


@dataclass
class Entity:
    """Phase A 의 경량 엔티티 표현 (G 의 Node('entity') 와 1:1 매핑)."""

    id: str
    canonical_name: str
    kind: EntityKind
    project_id: str
    track: Track
    mentions: int = 0
    scope: str | None = None


@dataclass
class CanonicalEntity(Entity):
    """linker 출력. 별칭 목록 포함."""

    aliases: list[str] = field(default_factory=list)


@dataclass
class Edge:
    """typed edge 조회 결과."""

    src: str
    dst: str
    relation_type: RelationType
    weight: float
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class FactRef:
    """`connected_facts()` 결과의 경량 fact 참조."""

    fact_id: str
    text: str
    source_namespace: str
