"""Projector 정식화 — Phase E2 (4090 Gemma4 26b-a4b 전용).

Selector 가 채운 지식 항성(fact 리스트)을 섹션 md 로 평면 펼침. **자유 생성 금지**
원칙 — fact 외의 주장은 verifier L1 이 탐지해 재진입.

`projection_host()` 는 4090 (`GP_OLLAMA_HOST`). 모델은 `PROJECTOR_MODEL` env.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable

from gstar.selector.gemma_client import OllamaChatClient, projection_host


_PROJECTOR_MODEL_DEFAULT = os.environ.get(
    "PROJECTOR_MODEL", "gemma4:26b-a4b-it-q4_K_M"
)


@dataclass
class SectionSpec:
    name: str
    instruction: str
    target_tokens: int = 400


@dataclass
class ProjectionInput:
    goal: str
    track: str                          # proposal | research | coding | document
    facts: list[dict]                   # {node_id, text, namespace, attrs}
    section: SectionSpec


@dataclass
class ProjectorOutput:
    section_name: str
    text: str
    used_fact_ids: list[str] = field(default_factory=list)
    model: str = ""


_SYSTEM = (
    "너는 Projector 이다. 오직 주어진 지식 조각(fact)만을 근거로 섹션을 작성한다. "
    "절대 새로운 사실·수치·인용을 만들어내지 마라. 각 주장 뒤에 [fact:<id>] 로 근거 fact id 를 표시한다. "
    "fact 에 없는 내용은 쓰지 않는다."
)


def _format_facts(facts: Iterable[dict], max_chars: int = 3500) -> str:
    lines = []
    total = 0
    for f in facts:
        fid = f.get("node_id") or ""
        t = f.get("text") or ""
        line = f"- [fact:{fid[:10]}] {t[:400]}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def project_section(
    inp: ProjectionInput,
    *,
    host: str | None = None,
    model: str | None = None,
    temperature: float = 0.2,
    num_predict: int | None = None,
    timeout_s: float = 60.0,
) -> ProjectorOutput:
    """fact → section md. fact 외 생성 금지."""
    host = host or projection_host()
    model = model or _PROJECTOR_MODEL_DEFAULT
    np = num_predict if num_predict is not None else max(256, int(inp.section.target_tokens * 1.5))
    client = OllamaChatClient(
        host=host,
        model=model,
        timeout_s=timeout_s,
        num_predict=np,
        temperature=temperature,
    )

    facts_block = _format_facts(inp.facts)
    prompt = (
        f"[목표] {inp.goal}\n"
        f"[트랙] {inp.track}\n"
        f"[섹션] {inp.section.name}\n"
        f"[지시] {inp.section.instruction}\n\n"
        f"[사용 가능한 fact]\n{facts_block}\n\n"
        f"위 fact 만을 근거로 {inp.section.target_tokens} 토큰 내외의 한국어 섹션을 작성. "
        f"각 주장 뒤에 [fact:<id 앞 10자>] 로 근거 표시."
    )
    try:
        text = client.judge(system=_SYSTEM, prompt=prompt)
    except Exception as exc:
        text = f"(Projector 호출 실패: {type(exc).__name__}: {exc})"
    return ProjectorOutput(
        section_name=inp.section.name,
        text=text.strip(),
        used_fact_ids=[str(f.get("node_id") or "") for f in inp.facts],
        model=model,
    )
