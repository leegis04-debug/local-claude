"""사업계획서(proposal) 트랙 — jw 워크플로우 포팅.

proposal 단계는 **양식이 있으면 양식 섹션 구조를 따른다**. form-ref.json + 양식
구조 메타를 `FormContext` 로 받아 `PROPOSAL_8` 기본값을 오버라이드.
"""

from __future__ import annotations

import json
from pathlib import Path

from gstar.entity.types import EntityKind
from gstar.projection.context_loader import FormContext
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
    # proposal = top-down: plan → award → dev → 검증 실험
    # idea~final-doc 는 계획 수립, award-to-dev/instruction/lab-note/lab-compare 는
    # 수주 후 실행 단계. 네 stage 는 research 와 동명이나 순서·의미가 반대.
    stages = [
        "idea",
        "debate",
        "structure",
        "spec",
        "risk-check",
        "experiment-plan",
        "proposal",
        "final-doc",
        # top-down 실행 단계
        "award-to-dev",     # 수주된 계획 → 개발 태스크/마일스톤 분해
        "instruction",      # 개발팀 지시서 (개별 태스크 상세)
        "lab-note",         # 개발·검증 실험 기록
        "lab-compare",      # 실험 비교·최종 수용 판정
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
    default_ollama_model = "gemma4:26b-a4b-it-q4_K_M"

    _SCHEMAS: dict[str, list[SectionSpec]] = {
        "idea": IDEA_3,
        "debate": DEBATE_4,
        "structure": STRUCTURE_7,
        "spec": SPEC_8,
        "risk-check": IDEA_3,
        "experiment-plan": STRUCTURE_7,
        "proposal": PROPOSAL_8,
        "final-doc": FINAL_DOC_8,
        # top-down 실행 단계 — 기본 스키마는 STRUCTURE_7 재사용.
        # 실제 사용 시 양식 또는 사용자 지시로 세분화 권장.
        "award-to-dev":   STRUCTURE_7,
        "instruction":    STRUCTURE_7,
        "lab-note":       IDEA_3,
        "lab-compare":    STRUCTURE_7,
    }

    def stage_role(self, stage: str) -> StageRole:
        return PROPOSAL_ROLES.get(stage, DEFAULT_ROLE)

    def section_schema(
        self,
        stage: str,
        form_context: FormContext | None = None,
    ) -> list[SectionSpec]:
        """기본 하드코딩 스키마. proposal 단계에 양식 meta 있으면 동적 대체."""
        if stage == "proposal" and form_context is not None:
            dynamic = _form_to_sections(form_context)
            if dynamic:
                return dynamic
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


_BUDGET_KEYWORDS = ("예산", "사업비", "비목", "인건비", "장비", "재료비", "간접비")
_PEOPLE_KEYWORDS = ("참여", "연구원", "담당자", "책임자", "컨소시엄", "조직", "체계", "인력")
_METRIC_KEYWORDS = ("KPI", "목표", "성과", "지표", "정량", "수치")
_TECH_KEYWORDS = ("기술", "개발", "알고리즘", "시스템", "아키텍처", "모델", "API")


def _required_kinds_from_title(title: str) -> tuple[list[str], list[str]]:
    """섹션 제목 → required_fact_kinds + required_entity_kinds 힌트."""
    fact_kinds: list[str] = []
    entity_kinds: list[str] = []
    t = title
    if any(k in t for k in _BUDGET_KEYWORDS):
        entity_kinds.append("budget")
    if any(k in t for k in _PEOPLE_KEYWORDS):
        entity_kinds.extend(["person", "org"])
    if any(k in t for k in _METRIC_KEYWORDS):
        fact_kinds.append("metric")
        entity_kinds.append("metric")
    if any(k in t for k in _TECH_KEYWORDS):
        entity_kinds.append("technology")
    return fact_kinds, entity_kinds


def _form_to_sections(form_context: FormContext) -> list[SectionSpec]:
    """FormContext.meta 에서 양식 sections 정보를 읽어 SectionSpec 리스트로 변환.

    지원 스키마 (둘 다 호환):
    1. form-ref.json 가 `{"sections": [{"number","title","guide"/"author_notes"...}]}`
       직접 포함하는 경우
    2. form-ref.json 에 `form_id` 만 있고 Gateway `/forms/{id}/structure` 에서
       가져온 JSON 을 `raw` 또는 meta["structure"] 로 가진 경우
    3. 아무것도 없으면 `raw` 텍스트에서 섹션 패턴 추출 시도

    충족 못하면 빈 리스트 반환 → 호출자가 PROPOSAL_8 폴백.
    """
    meta = form_context.meta or {}
    sections_data: list[dict] = []

    if isinstance(meta.get("sections"), list):
        sections_data = meta["sections"]
    elif isinstance(meta.get("structure"), dict):
        raw_sections = meta["structure"].get("sections")
        if isinstance(raw_sections, list):
            sections_data = raw_sections

    if not sections_data and form_context.raw:
        sections_data = _parse_sections_from_text(form_context.raw)

    if not sections_data:
        return []

    out: list[SectionSpec] = []
    for i, s in enumerate(sections_data):
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or s.get("name") or s.get("section_name") or "").strip()
        number = str(s.get("number") or s.get("section_number") or "").strip()
        if not title:
            continue
        display = f"{number}. {title}" if number else title
        guide_parts: list[str] = []
        for key in ("guide", "author_notes", "description", "instruction", "notes"):
            v = s.get(key)
            if v and isinstance(v, str):
                guide_parts.append(v.strip())
        guide = " / ".join(guide_parts)[:500] or f"{display} 섹션을 구체적으로 작성하시오."

        fact_kinds, entity_kinds = _required_kinds_from_title(title)
        if isinstance(s.get("required_fields"), list):
            for field_name in s["required_fields"]:
                if isinstance(field_name, str):
                    fk, ek = _required_kinds_from_title(field_name)
                    fact_kinds.extend(fk)
                    entity_kinds.extend(ek)

        min_w = int(s.get("min_words", 400))
        max_w = int(s.get("max_words", 3000))

        out.append(
            SectionSpec(
                id=f"form_{i+1:02d}",
                title=display,
                guide=guide,
                min_words=min_w,
                max_words=max_w,
                required_fact_kinds=list(dict.fromkeys(fact_kinds)),
                required_entity_kinds=list(dict.fromkeys(entity_kinds)),
            )
        )
    return out


def _parse_sections_from_text(raw: str) -> list[dict]:
    """최후 수단 — 양식 본문에서 `1-1.`, `1.`, `가.` 패턴 추출."""
    import re as _re

    out: list[dict] = []
    pattern = _re.compile(
        r"^\s*(?:(\d+(?:[-.]\d+)*\.?)\s+|([가-힣])\.\s+)([가-힣A-Za-z][^\n]{2,80})",
        _re.MULTILINE,
    )
    seen: set[str] = set()
    for m in pattern.finditer(raw):
        number = (m.group(1) or m.group(2) or "").strip()
        title = m.group(3).strip().rstrip(".:·")
        if len(title) < 3 or title in seen:
            continue
        seen.add(title)
        out.append({"number": number, "title": title})
        if len(out) >= 30:
            break
    return out
