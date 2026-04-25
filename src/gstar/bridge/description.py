"""
Description 템플릿 — 각 Issue Type 에 맞는 상세 섹션 구조.

원칙:
  - 작업자가 Jira 페이지만 보고도 착수 가능한 깊이 (배경·목적·방법론·DoD).
  - 빈 섹션은 `(해당 없음)` 명시 — placeholder 방치 금지.
  - 섹션 제목·번호는 고정 (automation.py / ri_scaffold.py 가 파싱 가능하게).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EpicDescription:
    scope_in: list[str] = field(default_factory=list)
    scope_out: list[str] = field(default_factory=list)
    gate_quant: list[str] = field(default_factory=list)
    gate_qual: list[str] = field(default_factory=list)
    main_milestone: str = ""
    resources: str = ""
    owners: str = ""
    deliverables: list[str] = field(default_factory=list)
    related_tasks: str = ""

    def render(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {x}" for x in items) if items else "- (해당 없음)"
        return (
            "## 1. 범위 (Scope)\n"
            f"**포함**\n{bullets(self.scope_in)}\n\n"
            f"**제외**\n{bullets(self.scope_out)}\n\n"
            "## 2. 완료 게이트 (Gate Criteria)\n"
            f"**정량 KPI**\n{bullets(self.gate_quant)}\n\n"
            f"**정성 기준**\n{bullets(self.gate_qual)}\n\n"
            "## 3. 기간·집중 자원\n"
            f"- Main Milestone: {self.main_milestone or '(해당 없음)'}\n"
            f"- 자원: {self.resources or '(해당 없음)'}\n"
            f"- 담당 주축: {self.owners or '(해당 없음)'}\n\n"
            "## 4. 산출물 체계\n"
            f"{bullets(self.deliverables)}\n\n"
            "## 5. 관련 Task 요약\n"
            f"{self.related_tasks or '(CSV generation 후 자동 채움)'}\n"
        )


@dataclass
class TaskDescription:
    background: str = ""
    purpose_rq: str = ""
    hypothesis_ids: list[str] = field(default_factory=list)
    methodology_steps: list[str] = field(default_factory=list)
    dod: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    references_public: list[str] = field(default_factory=list)
    references_internal: list[str] = field(default_factory=list)
    deps_blocker: list[str] = field(default_factory=list)
    deps_parallel: list[str] = field(default_factory=list)
    deps_unblocks: list[str] = field(default_factory=list)
    deps_paper_patent: list[str] = field(default_factory=list)
    ri_path: str = ""  # dev/ri/<KEY>/07-instruction.md

    def render(self) -> str:
        def bullets(items: list[str], empty_msg: str = "(해당 없음)") -> str:
            return "\n".join(f"- {x}" for x in items) if items else f"- {empty_msg}"

        def ol(items: list[str]) -> str:
            return "\n".join(f"{i+1}. {x}" for i, x in enumerate(items)) if items else "1. (해당 없음)"

        hyp = ", ".join(self.hypothesis_ids) if self.hypothesis_ids else "해당 없음"
        return (
            "## 1. 배경 (Background)\n"
            f"{self.background or '(3~5 줄의 상위 Epic·파이프라인 맥락·현 문제·리스크 필요)'}\n\n"
            "## 2. 목적 · 핵심 질문 (Purpose · RQ)\n"
            f"{self.purpose_rq or '(1~2 문장 핵심 질문 필요)'}\n"
            f"- 가설 ID: {hyp}\n\n"
            "## 3. 방법론 · 추진 순서 (Methodology)\n"
            f"{ol(self.methodology_steps)}\n\n"
            "## 4. 완료 기준 (Definition of Done)\n"
            f"{bullets([f'[ ] {x}' for x in self.dod] or ['[ ] 모든 Sub-task 완료 (Jira Done)',"
            " '[ ] 목표 KPI 달성', '[ ] PI 승인 코멘트'])}\n\n"
            "## 5. 산출물 (Deliverables)\n"
            f"{bullets(self.deliverables)}\n\n"
            "## 6. 참조 (References)\n"
            f"**공개 자료**\n{bullets(self.references_public)}\n\n"
            f"**내부 자산 (PI 승인 필요)**\n{bullets(self.references_internal)}\n\n"
            "## 7. 의존 관계 (Dependencies)\n"
            f"- 선행 블로커: {', '.join(self.deps_blocker) if self.deps_blocker else '없음'}\n"
            f"- 병렬 가능: {', '.join(self.deps_parallel) if self.deps_parallel else '없음'}\n"
            f"- 후행 해제: {', '.join(self.deps_unblocks) if self.deps_unblocks else '없음'}\n"
            f"- 논문·특허 연결: {', '.join(self.deps_paper_patent) if self.deps_paper_patent else '없음'}\n\n"
            "## 8. 상세 연구지시서\n"
            f"- 경로: `{self.ri_path or 'dev/ri/<JIRA_KEY>/07-instruction.md'}`\n"
            "- 작업자는 해당 지시서를 먼저 읽고 착수. 지시서와 본 Description 불일치 시 PI 에게 즉시 코멘트.\n"
        )


@dataclass
class SubtaskDescription:
    goal: str = ""
    steps: list[str] = field(default_factory=list)
    dod: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)

    def render(self) -> str:
        def bullets(items: list[str]) -> str:
            return "\n".join(f"- {x}" for x in items) if items else "- (해당 없음)"
        dod_items = [f"[ ] {x}" for x in self.dod] if self.dod else [
            "[ ] 산출물 경로 확정",
            "[ ] 일일 노트 링크 첨부",
            "[ ] Parent Task 로 인계 가능",
        ]
        return (
            "## 1. 목표\n"
            f"{self.goal or '(Parent Task 의 어느 Step 을 담당하는지 1문장)'}\n\n"
            "## 2. 실행 내용\n"
            f"{bullets(self.steps)}\n\n"
            "## 3. 완료 기준 (DoD)\n"
            f"{bullets(dod_items)}\n\n"
            "## 4. 산출물\n"
            f"{bullets(self.deliverables)}\n"
        )


__all__ = ["EpicDescription", "TaskDescription", "SubtaskDescription"]
