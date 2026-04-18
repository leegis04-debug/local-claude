"""사업계획서(proposal) 트랙 — jw 워크플로우 포팅."""

from __future__ import annotations

from gstar.entity.types import EntityKind
from gstar.projection.stage_roles import PROPOSAL_ROLES, DEFAULT_ROLE, StageRole
from gstar.projection.tasks.base import CoherenceRule, TaskDefinition, register
from gstar.projection.template import (
    DEBATE_4,
    FINAL_DOC_8,
    IDEA_3,
    PROPOSAL_8,
    SPEC_8,
    STRUCTURE_7,
    SectionSpec,
)


@register
class ProposalTask(TaskDefinition):
    name = "proposal"
    stages = [
        "idea",
        "debate",
        "structure",
        "spec",
        "risk-check",
        "experiment-plan",
        "proposal",
        "final-doc",
    ]
    entity_kinds = [
        EntityKind.PERSON,
        EntityKind.ORG,
        EntityKind.PROJECT,
        EntityKind.TECHNOLOGY,
        EntityKind.METRIC,
        EntityKind.BUDGET,
        EntityKind.TIMELINE,
        EntityKind.DOCUMENT,
    ]
    fact_kinds = ["metric", "decision", "constraint", "claim"]
    default_ollama_model = "gemma3:4b-it-qat"

    _SCHEMAS: dict[str, list[SectionSpec]] = {
        "idea": IDEA_3,
        "debate": DEBATE_4,
        "structure": STRUCTURE_7,
        "spec": SPEC_8,
        "risk-check": IDEA_3,
        "experiment-plan": STRUCTURE_7,
        "proposal": PROPOSAL_8,
        "final-doc": FINAL_DOC_8,
    }

    def stage_role(self, stage: str) -> StageRole:
        return PROPOSAL_ROLES.get(stage, DEFAULT_ROLE)

    def section_schema(self, stage: str) -> list[SectionSpec]:
        return list(self._SCHEMAS.get(stage, PROPOSAL_8))

    def coherence_rules(self, stage: str) -> list[CoherenceRule]:
        rules: list[CoherenceRule] = []
        if stage == "proposal":
            rules.append(
                CoherenceRule(
                    name="proposal_has_person",
                    applies_stages=["proposal"],
                    check=lambda text, reg, graph: "person" in text.lower()
                    or any(c in text for c in ["연구원", "책임자", "PI", "담당"]),
                )
            )
            rules.append(
                CoherenceRule(
                    name="proposal_has_budget",
                    applies_stages=["proposal"],
                    check=lambda text, reg, graph: any(
                        c in text for c in ["예산", "원", "억", "만원", "USD"]
                    ),
                )
            )
        return rules

    def output_format(self, stage: str) -> str:
        return "markdown"

    def system_prompt(self, stage: str) -> str:
        base = self.stage_role(stage).system_prompt
        return base or "당신은 정부 R&D 사업계획서 작성 전문가다."
