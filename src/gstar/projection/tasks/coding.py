"""개발·코딩(coding) 트랙 — explore→plan→implement→test→review 5 단계."""

from __future__ import annotations

from gstar.entity.types import EntityKind
from gstar.projection.stage_roles import CODING_ROLES, DEFAULT_ROLE, StageRole
from gstar.projection.tasks.base import CoherenceRule, TaskDefinition, register
from gstar.projection.template import CODING_STAGES, SectionSpec


@register
class CodingTask(TaskDefinition):
    name = "coding"
    stages = ["explore", "plan", "implement", "test", "review"]
    entity_kinds = [
        EntityKind.MODULE,
        EntityKind.CLASS,
        EntityKind.FUNCTION,
        EntityKind.VARIABLE,
        EntityKind.API_ENDPOINT,
        EntityKind.TEST_CASE,
        EntityKind.TECHNOLOGY,
    ]
    fact_kinds = ["decision", "constraint", "api_sig", "test_case"]
    default_ollama_model = "gemma4:26b-a4b-it-q4_K_M"

    def stage_role(self, stage: str) -> StageRole:
        return CODING_ROLES.get(stage, DEFAULT_ROLE)

    def section_schema(self, stage: str) -> list[SectionSpec]:
        return list(CODING_STAGES.get(stage, CODING_STAGES["plan"]))

    def coherence_rules(self, stage: str) -> list[CoherenceRule]:
        rules: list[CoherenceRule] = []
        if stage == "implement":
            rules.append(
                CoherenceRule(
                    name="impl_has_code_block",
                    applies_stages=["implement"],
                    check=lambda text, reg, graph: "```" in text or "def " in text or "class " in text,
                )
            )
        if stage == "review":
            rules.append(
                CoherenceRule(
                    name="review_mentions_tests",
                    applies_stages=["review"],
                    check=lambda text, reg, graph: "test" in text.lower(),
                )
            )
        return rules

    def output_format(self, stage: str) -> str:
        return "code" if stage == "implement" else "markdown"

    def system_prompt(self, stage: str) -> str:
        base = self.stage_role(stage).system_prompt
        return (
            base
            or "당신은 senior engineer. 기존 관례를 따르고, 불필요한 주석·docstring 금지."
        )
