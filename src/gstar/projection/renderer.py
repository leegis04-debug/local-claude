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
class PersonaDraft:
    role: str
    text: str


@dataclass
class RenderedSection:
    section: SectionSpec
    text: str
    attempts: int
    verdict: Verdict
    prompt_used: str
    persona_drafts: list[PersonaDraft] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.persona_drafts is None:
            self.persona_drafts = []


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
        from gstar.selector.gemma_client import OllamaChatClient, projection_host

        max_words = sec.section.max_words or 2000
        num_predict = min(8000, max(512, int(max_words * 2.2)))
        client = OllamaChatClient(
            host=projection_host(),
            model=model,
            timeout_s=180.0,
            num_predict=num_predict,
            temperature=0.3,
        )
    except Exception as e:
        return "", f"ollama unavailable: {e}"

    base_system = role.system_prompt or "당신은 전문 문서 작성자."
    system = (
        f"{base_system}\n"
        "\n"
        "[작성 규칙 — 엄수]\n"
        "1. 섹션 본문에 구체 수치·출처·경쟁사·차별점을 포함하라.\n"
        "2. <context> 내 <prev_stages>, <facts>, <entities>, <related> 블록은 **참고 자료**다. "
        "태그 이름이나 블록 내용을 본문에 그대로 복사·재출력하지 마라.\n"
        "3. 양식 지시문 (※·□ 시작, '필수 기재', '동의합니다', '[별지]' 등) 을 본문에 포함하지 마라.\n"
        "4. '이전 단계 요약', '확정 사실', '관련 엔티티' 같은 메타 제목을 본문에 쓰지 마라.\n"
        "5. 섹션 지침(<task>) 에 충실하게, 자체 논리 흐름으로 서술하라.\n"
        "6. 분량 범위를 지키고, 목록보다 서사 단락을 선호하되 수치는 정확히.\n"
    )
    prompt = (
        f"{sec.render()}\n\n"
        f"<output_instruction>\n"
        f"위 <task> 지침에 따라 '{sec.section.title}' 섹션 본문을 "
        f"{sec.section.min_words}~{sec.section.max_words}자로 작성. "
        "헤더(##)는 직접 쓰지 말 것 (merge 단계에서 자동 삽입). "
        "본문만 출력. 메타설명·태그·블록제목 금지.\n"
        f"</output_instruction>"
    )
    try:
        out = client.judge(system=system, prompt=prompt)
    except Exception as e:
        return "", f"ollama error: {e}"
    return out, prompt


def _call_ollama_with_persona(
    sec: SectionInput,
    role: StageRole,
    persona_system: str,
    model: str,
) -> tuple[str, str]:
    """페르소나 system prompt 로 드래프트 1회 생성."""
    try:
        from gstar.selector.gemma_client import OllamaChatClient, projection_host

        max_words = sec.section.max_words or 2000
        num_predict = min(6000, max(512, int(max_words * 1.6)))
        client = OllamaChatClient(
            host=projection_host(),
            model=model,
            timeout_s=180.0,
            num_predict=num_predict,
            temperature=0.4,
        )
    except Exception as e:
        return "", f"ollama unavailable: {e}"

    system = (
        f"{persona_system}\n\n"
        "규칙: <context> 는 참고만. 태그·블록제목·양식지시문 본문 금지. "
        f"분량 {sec.section.min_words}~{sec.section.max_words}자, 본문만 출력."
    )
    prompt = sec.render()
    try:
        out = client.judge(system=system, prompt=prompt)
    except Exception as e:
        return "", f"ollama error: {e}"
    return out, prompt


def _integrate_persona_drafts(
    sec: SectionInput,
    drafts: list["PersonaDraft"],
    model: str,
) -> str:
    """여러 페르소나 드래프트 → 통합본. integrator 페르소나 사용."""
    if not drafts:
        return ""
    if len(drafts) == 1:
        return drafts[0].text
    try:
        from gstar.projection.personas import integration_role
        from gstar.selector.gemma_client import OllamaChatClient, projection_host

        spec = integration_role()
        max_words = sec.section.max_words or 2000
        num_predict = min(8000, max(1024, int(max_words * 2.2)))
        client = OllamaChatClient(
            host=projection_host(),
            model=model,
            timeout_s=240.0,
            num_predict=num_predict,
            temperature=0.3,
        )
        parts = []
        for d in drafts:
            parts.append(f"<draft role=\"{d.role}\">\n{d.text.strip()}\n</draft>")
        system = (
            f"{spec.system_prompt}\n"
            "각 <draft> 의 강점을 유지하고 중복·충돌을 제거한 하나의 섹션 본문을 작성한다. "
            "헤더(##)·태그·메타설명 금지, 본문만."
        )
        prompt = (
            f"<task>섹션: {sec.section.title}\n지침: {sec.guide}</task>\n\n"
            + "\n\n".join(parts)
            + f"\n\n<output>{sec.section.min_words}~{sec.section.max_words}자 통합본:</output>"
        )
        out = client.judge(system=system, prompt=prompt)
        return out or drafts[-1].text
    except Exception:
        return drafts[-1].text


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
    stage: str = "",
) -> RenderedSection:
    if use_ollama is None:
        mode = os.environ.get("GP_RENDER_MODE", "auto").lower()
        use_ollama = mode != "stub"

    model = ollama_model or role.ollama_model
    use_personas = os.environ.get("GP_PERSONAS", "off").lower() == "on"

    persona_drafts: list[PersonaDraft] = []
    attempts = 0
    last_text = ""
    last_prompt = ""
    last_verdict = Verdict(ok=False, reason="not attempted")
    retries = max(0, role.coherence_retries)

    if use_personas and use_ollama and stage:
        try:
            from gstar.projection.personas import panel_for

            panel = panel_for(stage)
        except Exception:
            panel = []
        for spec in panel:
            draft_text, _ = _call_ollama_with_persona(sec, role, spec.system_prompt, model)
            if draft_text and draft_text.strip():
                persona_drafts.append(PersonaDraft(role=spec.role, text=draft_text))
        if persona_drafts:
            integrated = _integrate_persona_drafts(sec, persona_drafts, model)
            if integrated.strip():
                last_text = integrated
                last_prompt = f"(persona panel: {[d.role for d in persona_drafts]})"
                attempts = 1
                last_verdict = coherence_check(
                    last_text,
                    store, project_id, track,
                    required_entity_kinds=sec.section.required_entity_kinds,
                    required_fact_kinds=sec.section.required_fact_kinds,
                    use_ollama=False, coherence_mode=coherence_mode,
                )

    if not last_text:
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
                text, store, project_id, track,
                required_entity_kinds=sec.section.required_entity_kinds,
                required_fact_kinds=sec.section.required_fact_kinds,
                use_ollama=False, coherence_mode=coherence_mode,
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
        persona_drafts=persona_drafts,
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
