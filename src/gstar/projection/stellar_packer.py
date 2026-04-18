"""G cluster/search 결과 → 섹션별 입력 context 패킹.

Gemma 컨텍스트 보호: 각 섹션 입력 ≤ `max_chars` (기본 2500).
- cluster_id 있으면 `DuckStore.cluster_members()` 로 멤버+gravity 확보
- cluster_id 없으면 `GClient.search()` 결과 사용
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gstar.projection.summarizer import PrevSummary
from gstar.projection.template import SectionSpec
from gstar.storage.duckdb_store import DuckStore


@dataclass
class SectionInput:
    section: SectionSpec
    prev_summary_text: str
    retrieval_text: str
    fact_block: str
    entity_block: str
    guide: str
    max_chars: int = 2500

    def render(self) -> str:
        """LLM 프롬프트용 컨텍스트 블록.

        <context>...</context> 로 명시 격리. LLM 에게 '참고만, 재출력 금지' 신호.
        """
        parts = [
            "<task>",
            f"섹션 제목: {self.section.title}",
            f"작성 지침: {self.guide}",
            "</task>",
            "",
            "<context>",
            "[아래 자료는 참고용. 섹션 본문에 그대로 복사하지 말고, 논리·수치·출처를 재구성하라.]",
        ]
        if self.prev_summary_text:
            parts.append("")
            parts.append("<prev_stages>")
            parts.append(self.prev_summary_text)
            parts.append("</prev_stages>")
        if self.fact_block:
            parts.append("")
            parts.append("<facts>  <!-- 검증된 수치·결정·제약. 본문에 녹여서 활용 -->")
            parts.append(self.fact_block)
            parts.append("</facts>")
        if self.entity_block:
            parts.append("")
            parts.append("<entities>  <!-- 주요 인물·기관·기술. 구체성 확보용 -->")
            parts.append(self.entity_block)
            parts.append("</entities>")
        if self.retrieval_text:
            parts.append("")
            parts.append("<related>  <!-- 유사 사례·근거 -->")
            parts.append(self.retrieval_text)
            parts.append("</related>")
        parts.append("</context>")
        text = "\n".join(parts)
        if len(text) > self.max_chars:
            return text[: self.max_chars - 3] + "..."
        return text


def _format_fact_block(facts: list, max_items: int = 10) -> str:
    lines: list[str] = []
    for f in facts[:max_items]:
        lines.append(f"- [{f.kind}] {f.text} (from {f.source_stage})")
    return "\n".join(lines)


def _format_retrieval_block(hits: list, max_items: int = 6, max_each: int = 200) -> str:
    lines: list[str] = []
    for h in hits[:max_items]:
        if hasattr(h, "text"):
            text = h.text[:max_each].replace("\n", " ")
            lines.append(f"- {text}")
        elif isinstance(h, dict):
            text = (h.get("text") or "")[:max_each].replace("\n", " ")
            lines.append(f"- {text}")
    return "\n".join(lines)


def _format_entity_block(canonicals: list, max_items: int = 8) -> str:
    lines: list[str] = []
    for c in canonicals[:max_items]:
        lines.append(f"- [{c.kind.value}] {c.canonical_name} (x{c.mentions})")
    return "\n".join(lines)


def pack_sections(
    schema: list[SectionSpec],
    prev: PrevSummary,
    retrieval_hits: list,
    entities: list,
    *,
    max_chars_per_section: int = 2500,
    prev_block_chars: int = 1200,
) -> list[SectionInput]:
    prev_text = prev.as_prompt_block(max_chars=prev_block_chars)
    sections: list[SectionInput] = []
    for spec in schema:
        relevant_facts = [
            f for f in prev.facts if not spec.required_fact_kinds or f.kind in spec.required_fact_kinds
        ]
        if not relevant_facts:
            relevant_facts = prev.facts[:8]

        relevant_entities = [
            c
            for c in entities
            if not spec.required_entity_kinds
            or c.kind.value in spec.required_entity_kinds
        ]
        if not relevant_entities:
            relevant_entities = entities[:8]

        sections.append(
            SectionInput(
                section=spec,
                prev_summary_text=prev_text,
                retrieval_text=_format_retrieval_block(retrieval_hits),
                fact_block=_format_fact_block(relevant_facts),
                entity_block=_format_entity_block(relevant_entities),
                guide=spec.guide,
                max_chars=max_chars_per_section,
            )
        )
    return sections
