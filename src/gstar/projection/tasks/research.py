"""연구개발(research) 트랙 — re 워크플로우 + lab_tracker 통합 훅."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gstar.entity.types import EntityKind
from gstar.projection.stage_roles import DEFAULT_ROLE, RESEARCH_ROLES, StageRole
from gstar.projection.tasks.base import CoherenceRule, IngestHook, TaskDefinition, register
from gstar.projection.template import (
    DEBATE_4,
    FINAL_DOC_8,
    IDEA_3,
    PROPOSAL_8,
    RESEARCH_9,
    SPEC_8,
    STRUCTURE_7,
    SectionSpec,
)


def _lab_tracker_hook(project_dir: str, **kwargs: Any) -> dict:
    """~/workspace/ai/lib/lab_tracker/experiments/*/experiment.json 을 흡수해
    EXPERIMENT/RESULT entity · metric fact 를 반환.

    반환: {"facts": [...], "experiments": [...]}
    실제 entity/fact 생성은 호출자(pipeline) 가 수행.
    """
    lab_root = Path.home() / "workspace" / "ai" / "lib" / "lab_tracker" / "experiments"
    out_experiments: list[dict] = []
    if not lab_root.exists():
        return {"experiments": [], "facts": []}
    for exp_dir in sorted(lab_root.iterdir()):
        meta = exp_dir / "experiment.json"
        if not meta.exists():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out_experiments.append(
            {
                "name": data.get("name") or exp_dir.name,
                "dataset": data.get("dataset"),
                "method": data.get("method"),
                "metrics": data.get("metrics", {}),
                "path": str(exp_dir),
            }
        )
    return {"experiments": out_experiments, "facts": []}


@register
class ResearchTask(TaskDefinition):
    name = "research"
    stages = [
        "idea",
        "debate",
        "structure",
        "spec",
        "risk-check",
        "experiment-plan",
        "proposal",
        "final-doc",
        "lab-note",
    ]
    entity_kinds = [
        EntityKind.PERSON,
        EntityKind.ORG,
        EntityKind.PROJECT,
        EntityKind.TECHNOLOGY,
        EntityKind.METRIC,
        EntityKind.HYPOTHESIS,
        EntityKind.METHOD,
        EntityKind.DATASET,
        EntityKind.EXPERIMENT,
        EntityKind.RESULT,
        EntityKind.CITATION,
    ]
    fact_kinds = ["metric", "decision", "constraint", "hypothesis", "result"]
    default_ollama_model = "gemma3:4b-it-qat"

    _SCHEMAS: dict[str, list[SectionSpec]] = {
        "idea": IDEA_3,
        "debate": DEBATE_4,
        "structure": STRUCTURE_7,
        "spec": SPEC_8,
        "risk-check": IDEA_3,
        "experiment-plan": STRUCTURE_7,
        "proposal": RESEARCH_9,
        "final-doc": FINAL_DOC_8,
        "lab-note": RESEARCH_9,
    }

    def stage_role(self, stage: str) -> StageRole:
        return RESEARCH_ROLES.get(stage, DEFAULT_ROLE)

    def section_schema(self, stage: str) -> list[SectionSpec]:
        return list(self._SCHEMAS.get(stage, RESEARCH_9))

    def coherence_rules(self, stage: str) -> list[CoherenceRule]:
        rules: list[CoherenceRule] = []
        if stage in {"experiment-plan", "proposal", "lab-note"}:
            rules.append(
                CoherenceRule(
                    name="hypothesis_method_chain",
                    applies_stages=[stage],
                    check=lambda text, reg, graph: (
                        ("가설" in text or "hypothesis" in text.lower())
                        and ("방법" in text or "method" in text.lower())
                    ),
                )
            )
        return rules

    def ingest_hooks(self) -> list[IngestHook]:
        return [IngestHook("lab_tracker", _lab_tracker_hook)]

    def output_format(self, stage: str) -> str:
        return "markdown"

    def system_prompt(self, stage: str) -> str:
        base = self.stage_role(stage).system_prompt
        return base or "당신은 연구 과학자. 가설·방법·실험·결과 체인을 유지."
