"""범용 문서(document) 트랙 — outline→draft→revise→finalize 4 단계."""

from __future__ import annotations

from gstar.entity.types import EntityKind
from gstar.projection.stage_roles import DEFAULT_ROLE, DOCUMENT_ROLES, StageRole
from gstar.projection.tasks.base import CoherenceRule, TaskDefinition, register
from gstar.projection.template import DOCUMENT_STAGES, SectionSpec


@register
class DocumentTask(TaskDefinition):
    name = "document"
    stages = ["outline", "draft", "revise", "finalize"]
    entity_kinds = [
        EntityKind.PERSON,
        EntityKind.ORG,
        EntityKind.CITATION,
        EntityKind.CLAIM,
        EntityKind.DOCUMENT,
        EntityKind.TIMELINE,
    ]
    fact_kinds = ["claim", "decision", "constraint"]
    default_ollama_model = "gemma3:4b-it-qat"

    def stage_role(self, stage: str) -> StageRole:
        return DOCUMENT_ROLES.get(stage, DEFAULT_ROLE)

    def section_schema(self, stage: str) -> list[SectionSpec]:
        return list(DOCUMENT_STAGES.get(stage, DOCUMENT_STAGES["draft"]))

    def coherence_rules(self, stage: str) -> list[CoherenceRule]:
        rules: list[CoherenceRule] = []
        if stage in {"revise", "finalize"}:
            rules.append(
                CoherenceRule(
                    name="claims_have_citations",
                    applies_stages=[stage],
                    check=lambda text, reg, graph: (
                        ("[" in text and "]" in text)
                        or "doi:" in text
                        or "http" in text
                    ),
                )
            )
        return rules

    def output_format(self, stage: str) -> str:
        return "markdown"

    def system_prompt(self, stage: str) -> str:
        base = self.stage_role(stage).system_prompt
        return base or "당신은 writer. 독자·목적·구조·인용 순으로 명확하게."
