"""Phase E4 — Pipeline 조립 (PROJECTION_MODE env).

모드:
  classic — 기존 직접 LLM (구현 X, 기존 projection/cli.py 가 classic 역할)
  sv      — Selector + Projector
  svr     — + Verifier L1
  svrr    — + Verifier L2 (기존 RAG 대조) + Reinforce  (권장)

사용:
  from gstar.projection.pipeline_e import run_svrr
  out = run_svrr(goal="AI 농업 플랫폼", track="proposal", section=..., gclient=...)
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any

from gstar.projection.projector import ProjectionInput, ProjectorOutput, SectionSpec, project_section
from gstar.projection.reinforce import ReinforceResult, reinforce_to_gateway
from gstar.projection.selector_loop import SelectorResult, run_selector
from gstar.projection.verifier import (
    VerificationReport,
    apply_trust_deltas,
    verify_l1,
    verify_l2_rag_cross,
)


def _project_panel(proj_in, *, track_name: str, rep) -> ProjectorOutput:
    """Phase E2+Panel — stage 별 persona 패널로 projector 다중 호출 후 integrator 합성.

    각 persona 는 project_section 을 재사용하되 ProjectionInput.section.instruction
    앞에 persona system prompt 를 prepend 해 관점을 강제. integrator 는 drafts 전체를
    받아 합성.

    panel 이 비어 있으면 (stage 에 panel 정의 없음) 단일 project_section 로 폴백.
    """
    from gstar.projection.personas import panel_for, integration_role

    stage = proj_in.section.name
    panel = panel_for(stage)
    if not panel:
        # stage 에 panel 정의 없음 — 단일 호출 폴백
        rep.warnings.append(f"[panel] stage='{stage}' panel 미정의 → 단일 호출")
        return project_section(proj_in)

    drafts: list[tuple[str, str]] = []  # (role, text)
    for spec in panel:
        persona_in = ProjectionInput(
            goal=proj_in.goal,
            track=proj_in.track,
            facts=proj_in.facts,
            section=SectionSpec(
                name=f"{stage}::{spec.role}",
                instruction=f"[역할: {spec.role}] {spec.system_prompt}\n\n{proj_in.section.instruction}",
                target_tokens=proj_in.section.target_tokens,
            ),
        )
        try:
            out = project_section(persona_in)
            if out.text and "(Projector 호출 실패" not in out.text:
                drafts.append((spec.role, out.text))
            else:
                rep.warnings.append(f"[panel] {spec.role} 드래프트 실패 — 제외")
        except Exception as exc:
            rep.warnings.append(f"[panel] {spec.role}: {type(exc).__name__}: {exc}")

    if not drafts:
        rep.warnings.append("[panel] 모든 persona 실패 → 단일 호출 폴백")
        return project_section(proj_in)

    # integrator 로 합성
    integ = integration_role()
    integrated_block = "\n\n".join(f"## {role} 관점\n{text}" for role, text in drafts)
    integ_in = ProjectionInput(
        goal=proj_in.goal,
        track=proj_in.track,
        facts=proj_in.facts,
        section=SectionSpec(
            name=f"{stage}::integrated",
            instruction=(
                f"[역할: integrator] {integ.system_prompt}\n\n"
                f"[원래 섹션 지시] {proj_in.section.instruction}\n\n"
                f"[{len(drafts)}개 persona 드래프트]\n{integrated_block}\n\n"
                "위 관점들을 하나의 일관된 섹션으로 합쳐라. 각 persona 의 강점 유지, 중복·충돌 제거."
            ),
            target_tokens=int(proj_in.section.target_tokens * 1.5),
        ),
    )
    try:
        integrated = project_section(integ_in)
        # 원래 섹션명으로 복원
        integrated.section_name = stage
        return integrated
    except Exception as exc:
        rep.warnings.append(f"[panel] integrator 실패: {exc} → 첫 draft 사용")
        role0, text0 = drafts[0]
        return ProjectorOutput(
            section_name=stage,
            text=text0,
            used_fact_ids=[str(f.get("node_id") or "") for f in proj_in.facts],
            model=f"panel-fallback-{role0}",
        )


@dataclass
class PipelineResult:
    mode: str
    goal: str
    track: str
    section: str
    selector: SelectorResult | None = None
    projector: ProjectorOutput | None = None
    verifier_l1: VerificationReport | None = None
    verifier_l2: VerificationReport | None = None
    reinforce: ReinforceResult | None = None
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = {
            "mode": self.mode,
            "goal": self.goal,
            "track": self.track,
            "section": self.section,
            "warnings": self.warnings,
        }
        if self.selector is not None:
            d["selector"] = {
                "final_facts_count": len(self.selector.final_facts),
                "converged": self.selector.converged,
                "iterations": [asdict(i) for i in self.selector.iterations],
            }
        if self.projector is not None:
            d["projector"] = {
                "section": self.projector.section_name,
                "used_fact_ids": self.projector.used_fact_ids,
                "model": self.projector.model,
                "text_preview": self.projector.text[:300],
            }
        if self.verifier_l1 is not None:
            d["verifier_l1"] = self.verifier_l1.to_json()
        if self.verifier_l2 is not None:
            d["verifier_l2"] = self.verifier_l2.to_json()
        if self.reinforce is not None:
            d["reinforce"] = {
                "scanned": self.reinforce.scanned,
                "eligible": self.reinforce.eligible,
                "pushed": self.reinforce.pushed,
                "failed": self.reinforce.failed,
            }
        return d


def run_pipeline(
    goal: str,
    *,
    track: str,
    section: SectionSpec,
    gclient,
    store=None,
    mode: str | None = None,
    selector_kwargs: dict | None = None,
    reinforce_min_trust: float = 3.0,
) -> PipelineResult:
    """mode: "sv" | "svr" | "svrr". None → env PROJECTION_MODE 또는 "svrr"."""
    mode = mode or os.environ.get("PROJECTION_MODE", "svrr")
    rep = PipelineResult(mode=mode, goal=goal, track=track, section=section.name)

    # --- Selector ---
    skwargs = selector_kwargs or {}
    sel = run_selector(goal, gclient=gclient, **skwargs)
    rep.selector = sel
    if not sel.final_facts:
        rep.warnings.append("Selector 가 빈 fact 집합을 반환 — Projector skip")
        return rep

    # --- Projector ---
    proj_in = ProjectionInput(goal=goal, track=track, facts=sel.final_facts, section=section)
    use_panel = os.environ.get("GP_PERSONAS", "off").lower() in {"on", "1", "true"}
    if use_panel:
        proj_out = _project_panel(proj_in, track_name=track, rep=rep)
    else:
        proj_out = project_section(proj_in)
    rep.projector = proj_out

    if mode == "sv":
        return rep

    # --- Verifier L1 ---
    l1 = verify_l1(
        section_text=proj_out.text,
        facts=[
            {
                "node_id": f.get("node_id"),
                "text": f.get("text", ""),
                "attrs": {"source": f.get("namespace")},
                "created_at": None,
                "entity_kinds": [],
            }
            for f in sel.final_facts
        ],
        track=track,
    )
    rep.verifier_l1 = l1
    if not l1.pass_:
        rep.warnings.append(f"L1 실패 — retry_hint: {l1.retry_hint}")

    if mode == "svr":
        return rep

    # --- Verifier L2 (RAG cross) ---
    l2 = verify_l2_rag_cross(
        facts=[
            {"node_id": f.get("node_id"), "text": f.get("text", "")}
            for f in sel.final_facts
        ]
    )
    rep.verifier_l2 = l2
    if store is not None and l2.trust_deltas:
        try:
            apply_trust_deltas(store, l2, layer="L2")
        except Exception as exc:
            rep.warnings.append(f"apply_trust_deltas 실패: {exc}")

    # --- Reinforce ---
    if store is not None and os.environ.get("REINFORCE_ENABLED", "on").lower() in {"on", "1", "true"}:
        try:
            r = reinforce_to_gateway(store, min_trust=reinforce_min_trust)
            rep.reinforce = r
        except Exception as exc:
            rep.warnings.append(f"reinforce 실패: {exc}")

    return rep
