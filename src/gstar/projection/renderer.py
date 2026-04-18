"""섹션별 Ollama 호출 + coherence gate 재시도 루프.

Ollama 미가용 또는 `GP_RENDER_MODE=stub` 이면 섹션 입력 자체를 출력 (테스트·오프라인용).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from gstar.projection.coherence_gate import Verdict, check as coherence_check
from gstar.projection.stage_roles import StageRole
from gstar.projection.stellar_packer import SectionInput
from gstar.projection.template import SectionSpec
from gstar.storage.duckdb_store import DuckStore


@dataclass
class RenderedSection:
    section: SectionSpec
    text: str
    attempts: int
    verdict: Verdict
    prompt_used: str


def _render_stub(sec: SectionInput) -> str:
    """오프라인 fallback — 입력 context 만 요약 형태로 노출."""
    lines = [
        f"## {sec.section.title}",
        "",
        f"> {sec.guide}",
        "",
        "### 이전 단계 요약",
        sec.prev_summary_text or "(없음)",
        "",
        "### 확정 사실",
        sec.fact_block or "(없음)",
        "",
        "### 관련 엔티티",
        sec.entity_block or "(없음)",
        "",
        "### 근거",
        sec.retrieval_text or "(없음)",
    ]
    return "\n".join(lines)


def _call_ollama(
    sec: SectionInput, role: StageRole, model: str
) -> tuple[str, str]:
    try:
        from gstar.selector.gemma_client import OllamaChatClient

        client = OllamaChatClient(model=model)
    except Exception as e:
        return "", f"ollama unavailable: {e}"

    system = role.system_prompt or "당신은 문서 작성자. 구체적이고 정량적으로."
    prompt = (
        f"{sec.render()}\n\n"
        f"==== 위 섹션 '{sec.section.title}' 을 {sec.section.min_words}-{sec.section.max_words}자 범위로 작성하세요. ===="
    )
    try:
        out = client.judge(system=system, prompt=prompt)
    except Exception as e:
        return "", f"ollama error: {e}"
    return out, prompt


def render_section(
    sec: SectionInput,
    role: StageRole,
    store: DuckStore,
    project_id: str,
    track: str,
    *,
    use_ollama: bool | None = None,
    coherence_mode: str = "strict",
    ollama_model: str | None = None,
) -> RenderedSection:
    if use_ollama is None:
        mode = os.environ.get("GP_RENDER_MODE", "auto").lower()
        use_ollama = mode != "stub"

    model = ollama_model or role.ollama_model

    attempts = 0
    last_text = ""
    last_prompt = ""
    last_verdict = Verdict(ok=False, reason="not attempted")
    retries = max(0, role.coherence_retries)

    while attempts <= retries:
        attempts += 1
        if use_ollama:
            text, prompt_used = _call_ollama(sec, role, model)
            if not text:
                text = _render_stub(sec)
                prompt_used = "(ollama failed, stub)"
        else:
            text = _render_stub(sec)
            prompt_used = "(stub render)"

        verdict = coherence_check(
            text,
            store,
            project_id,
            track,
            required_entity_kinds=sec.section.required_entity_kinds,
            required_fact_kinds=sec.section.required_fact_kinds,
            use_ollama=False,
            coherence_mode=coherence_mode,
        )
        last_text = text
        last_prompt = prompt_used
        last_verdict = verdict
        if verdict.ok or attempts > retries:
            break

    return RenderedSection(
        section=sec.section,
        text=last_text,
        attempts=attempts,
        verdict=last_verdict,
        prompt_used=last_prompt,
    )


def merge(
    sections: list[RenderedSection],
    *,
    heading_level: int = 2,
) -> str:
    out: list[str] = []
    prefix = "#" * heading_level
    for rs in sections:
        if not rs.text.lstrip().startswith("#"):
            out.append(f"{prefix} {rs.section.title}")
            out.append("")
        out.append(rs.text.rstrip())
        out.append("")
    return "\n".join(out).rstrip() + "\n"
