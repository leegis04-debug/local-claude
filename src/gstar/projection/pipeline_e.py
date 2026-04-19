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
