"""
bridge.yaml 로더 — 프로젝트별 Jira 설정 + 팀 매핑 + Fix Version / Component / Label 어휘.

bridge.yaml 스키마 예:

```yaml
project:
  key: DT                # Jira 프로젝트 키
  name: Deep-Tect
  site: lee35460.atlassian.net
  slug: deep-tect

team:
  pi:
    display_name: 이재원
    email: lee35460@gmail.com
  members:
    - key: R
      display_name: 송주한
      email: wngks10144@gmail.com

fix_versions:
  - id: M1-Q2-26
    label: "Milestone 1 — Q2'26"
    due: 2026-06-30
    gate: "IoU ≥ 0.80 · 미검출률 ≤ 5%"
  - id: M2-Q3-26
    label: "Milestone 2 — Q3'26"
    due: 2026-09-30
    gate: "Top-3 ≥ 85%"
  - id: M3-Q4-26
    label: "Milestone 3 — Q4'26"
    due: 2026-12-31
    gate: "원인 Top-1 ≥ 75% · 심각도 F1 ≥ 0.80"

components:
  - "Phase 1.1"
  - "Phase 1.2"
  # ...

labels:
  domain: [vision, language, reasoning, integration, field]
  kind: [paper, patent, infra, pm, research-skill, tracker, hypothesis]
  tech: [sam2, clip, vlm, self-h100, kait-h100, rtx5060]

epics:
  - ext_id: DT-E1
    summary: "Vision Model — Region-level 이상 분리"
    labels: [vision, sam2]
    priority: Highest
    epic_name: "Vision 1단계"
  # ...

custom_fields:
  story_points: true        # Jira 기본 제공
  iou: true                 # 프로젝트마다 필요시 선택
  top3_match: true
  cause_top1: true
  severity_f1: true
  edge_latency_ms: true
  gpu_hours: true
  hypothesis: [H1, H2, H3, H4, H5]
  paper_role: true
  paper_venue_grade: true
  patent_role: true
  patent_id: true
```

로더는 필수 필드 검증을 수행. 누락 시 ValueError 로 즉시 실패 (조용한 스킵 금지).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as _e:  # pragma: no cover
    yaml = None  # type: ignore


@dataclass
class TeamMember:
    key: str
    display_name: str
    email: str


@dataclass
class FixVersion:
    id: str
    label: str
    due: str
    gate: str = ""
    start: str = ""


@dataclass
class EpicDef:
    ext_id: str
    summary: str
    labels: list[str] = field(default_factory=list)
    priority: str = "High"
    epic_name: str = ""
    description: str = ""
    color: str = ""    # Jira issue color (purple/blue/green/teal/yellow/orange/grey + dark_*)


@dataclass
class BridgeConfig:
    project_key: str
    project_name: str
    project_slug: str
    jira_site: str
    pi: TeamMember
    members: list[TeamMember]
    fix_versions: list[FixVersion]
    components: list[str]
    labels_vocab: dict[str, list[str]]
    epics: list[EpicDef]
    custom_fields: dict[str, Any]
    raw: dict[str, Any]

    def member_by_key(self, key: str) -> TeamMember:
        if key.upper() in {"PI", self.pi.key.upper()}:
            return self.pi
        for m in self.members:
            if m.key.upper() == key.upper():
                return m
        raise KeyError(f"팀 멤버 key='{key}' 가 bridge.yaml 에 정의되지 않음")

    def resolve_assignee(self, ref: str) -> str:
        """CSV Assignee 컬럼에 들어갈 문자열 (Jira displayName 우선)."""
        return self.member_by_key(ref).display_name


def load_config(path: str | Path) -> BridgeConfig:
    if yaml is None:
        raise RuntimeError(
            "PyYAML 미설치. `pip install pyyaml` 후 재시도하거나 "
            "local-claude/pyproject.toml 의존성을 확인."
        )
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"bridge.yaml 없음: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    proj = raw.get("project") or {}
    team = raw.get("team") or {}

    pi_raw = team.get("pi") or {}
    if not pi_raw.get("display_name"):
        raise ValueError("bridge.yaml: team.pi.display_name 필수")
    pi = TeamMember(
        key=pi_raw.get("key", "PI"),
        display_name=pi_raw["display_name"],
        email=pi_raw.get("email", ""),
    )
    members = [
        TeamMember(
            key=m.get("key") or m["display_name"][:1],
            display_name=m["display_name"],
            email=m.get("email", ""),
        )
        for m in (team.get("members") or [])
    ]

    fvs = [
        FixVersion(
            id=fv["id"],
            label=fv.get("label", fv["id"]),
            due=str(fv.get("due", "")),
            gate=fv.get("gate", ""),
            start=str(fv.get("start", "")),
        )
        for fv in (raw.get("fix_versions") or [])
    ]
    if not fvs:
        raise ValueError("bridge.yaml: fix_versions 최소 1개 필요")

    eps = [
        EpicDef(
            ext_id=e["ext_id"],
            summary=e["summary"],
            labels=list(e.get("labels") or []),
            priority=e.get("priority", "High"),
            epic_name=e.get("epic_name", e["summary"]),
            description=e.get("description", ""),
            color=e.get("color", ""),
        )
        for e in (raw.get("epics") or [])
    ]

    return BridgeConfig(
        project_key=proj.get("key", "P"),
        project_name=proj.get("name", "Project"),
        project_slug=proj.get("slug", proj.get("name", "project")).lower().replace(" ", "-"),
        jira_site=proj.get("site", ""),
        pi=pi,
        members=members,
        fix_versions=fvs,
        components=list(raw.get("components") or []),
        labels_vocab=dict(raw.get("labels") or {}),
        epics=eps,
        custom_fields=dict(raw.get("custom_fields") or {}),
        raw=raw,
    )


def example_config_yaml(project_key: str = "DT", project_name: str = "Project") -> str:
    """`bridge.yaml` skeleton — `bin/bridge init` 가 생성해서 쓴다."""
    return f"""# bridge.yaml — 프로젝트 Jira 브리지 설정 (award-to-dev 입력)
# 참조: local-claude/docs/ops-bridge.md

project:
  key: {project_key}
  name: {project_name}
  slug: {project_name.lower().replace(' ', '-')}
  site: your-org.atlassian.net    # Jira Cloud 사이트

team:
  pi:
    key: PI
    display_name: 홍길동
    email: pi@example.com
  members:
    - key: R
      display_name: 김연구
      email: r@example.com

fix_versions:
  - id: M1-Q2-26
    label: "Milestone 1 — Q2'26"
    start: 2026-04-01    # 선택 — Jira Fix Version Start date 로 들어감 (bin/bridge provision)
    due: 2026-06-30      # Jira Fix Version Release date
    gate: "주요 KPI 1 달성 · PI 승인"

components:
  - "Phase 1.1"
  - "Phase 1.2"
  - "Phase 1.3"

labels:
  domain: [core]
  kind: [paper, patent, infra, pm, research-skill, tracker, hypothesis]
  tech: []

epics:
  - ext_id: {project_key}-E1
    summary: "Epic 1 — <핵심 축>"
    labels: [core]
    priority: Highest
    epic_name: "Epic 1"
    description: |
      ## 1. 범위
      - 포함: ...
      ## 2. 완료 게이트
      - KPI: ...

custom_fields:
  story_points: true
  hypothesis: [H1, H2, H3, H4, H5]
"""


__all__ = ["BridgeConfig", "TeamMember", "FixVersion", "EpicDef", "load_config", "example_config_yaml"]
