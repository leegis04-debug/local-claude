"""TaskDefinition ABC — 트랙 플러그인 베이스."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Literal

from gstar.entity.types import EntityKind
from gstar.projection.stage_roles import DEFAULT_ROLE, StageRole
from gstar.projection.template import SectionSpec


OutputFormat = Literal["markdown", "code", "hwpx", "docx"]


class CoherenceRule:
    def __init__(
        self,
        name: str,
        applies_stages: list[str],
        check,
    ):
        self.name = name
        self.applies_stages = applies_stages
        self.check = check


class IngestHook:
    """트랙별 산출물 역삽입 훅."""

    def __init__(self, name: str, run):
        self.name = name
        self.run = run


class TaskDefinition(ABC):
    name: ClassVar[str] = "base"
    stages: ClassVar[list[str]] = []
    entity_kinds: ClassVar[list[EntityKind]] = []
    fact_kinds: ClassVar[list[str]] = ["decision", "metric", "constraint"]
    default_ollama_model: ClassVar[str] = "gemma3:4b-it-qat"

    @abstractmethod
    def stage_role(self, stage: str) -> StageRole:  # pragma: no cover
        ...

    @abstractmethod
    def section_schema(self, stage: str) -> list[SectionSpec]:  # pragma: no cover
        ...

    def coherence_rules(self, stage: str) -> list[CoherenceRule]:
        return []

    def ingest_hooks(self) -> list[IngestHook]:
        return []

    def output_format(self, stage: str) -> OutputFormat:
        return "markdown"

    def system_prompt(self, stage: str) -> str:
        role = self.stage_role(stage)
        return role.system_prompt or ""


_REGISTRY: dict[str, type[TaskDefinition]] = {}


def register(cls: type[TaskDefinition]) -> type[TaskDefinition]:
    _REGISTRY[cls.name] = cls
    return cls


def get_task(name: str) -> TaskDefinition:
    if name not in _REGISTRY:
        raise KeyError(f"unknown track: {name}. registered={sorted(_REGISTRY)}")
    return _REGISTRY[name]()


def list_tasks() -> list[str]:
    return sorted(_REGISTRY.keys())


def _default_role(stage: str, fallback_role: StageRole | None = None) -> StageRole:
    return fallback_role or DEFAULT_ROLE
