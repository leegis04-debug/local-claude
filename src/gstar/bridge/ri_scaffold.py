"""
연구지시서 (RI) 폴더 scaffolding — `dev/ri/<JIRA-KEY>/` 아래 8 단 template 복제.

deep-tect ri-gen.py 규약 답습:
  00-jira-meta.md · 01-idea.md · 02-debate.md · 03-structure.md
  04-spec.md · 05-risk.md · 06-experiment.md · 07-instruction.md · 08-report.md

템플릿 소스:
  1. env `RI_TEMPLATE_DIR` 명시
  2. `<project>/09-bridge/dev/ri/_template/` (프로젝트 local 오버라이드)
  3. `local-claude/src/gstar/bridge/templates/ri/_template/` (기본값)

입력 placeholder 치환:
  {{JIRA_KEY}}·{{JIRA_KEY_SHORT}}·{{JIRA_URL}}·{{SUMMARY}}·{{ASSIGNEE}}
  {{DUE_DATE}}·{{START_DATE}}·{{EPIC_KEY}}·{{EPIC_SUMMARY}}·{{PHASE}}
  {{LABELS}}·{{FIX_VERSION}}·{{PRIORITY}}·{{PROJECT_NAME}}·{{GPU_ALLOCATION}}
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


STAGE_ORDER = [
    "01-idea.md", "02-debate.md", "03-structure.md",
    "04-spec.md", "05-risk.md", "06-experiment.md",
    "07-instruction.md", "08-report.md",
]


@dataclass
class RIContext:
    jira_key: str               # import 완료 후 실제 Jira Key (예: DT-83). 없으면 External ID.
    project_key: str            # DT
    summary: str
    assignee: str
    due_date: str
    epic_key: str
    epic_summary: str
    phase: str
    labels: str                 # "vision, sam2, hypothesis"
    fix_version: str
    priority: str
    project_name: str
    jira_site: str = ""
    gpu_allocation: str = "자체 H100 1대 — PI 승인"

    @property
    def jira_url(self) -> str:
        if self.jira_site:
            return f"https://{self.jira_site}/browse/{self.jira_key}"
        return f"(jira_site 미설정) {self.jira_key}"

    @property
    def jira_key_short(self) -> str:
        prefix = f"{self.project_key}-"
        return self.jira_key[len(prefix):] if self.jira_key.startswith(prefix) else self.jira_key

    def placeholders(self) -> dict[str, str]:
        return {
            "JIRA_KEY": self.jira_key,
            "JIRA_KEY_SHORT": self.jira_key_short,
            "JIRA_URL": self.jira_url,
            "SUMMARY": self.summary,
            "ASSIGNEE": self.assignee,
            "DUE_DATE": self.due_date,
            "START_DATE": datetime.now().strftime("%Y-%m-%d"),
            "MID_DATE": "-",
            "ISSUE_DATE": datetime.now().strftime("%Y-%m-%d"),
            "EPIC_KEY": self.epic_key or "-",
            "EPIC_SUMMARY": self.epic_summary or "-",
            "PHASE": self.phase or "-",
            "LABELS": self.labels or "-",
            "FIX_VERSION": self.fix_version or "-",
            "PRIORITY": self.priority or "-",
            "PROJECT_NAME": self.project_name,
            "GPU_ALLOCATION": self.gpu_allocation,
        }


def resolve_template_dir(project_dir: str | Path) -> Path:
    env = os.environ.get("RI_TEMPLATE_DIR")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
    local = Path(project_dir) / "09-bridge" / "dev" / "ri" / "_template"
    if local.exists() and any(local.iterdir()):
        return local
    # 기본: local-claude 모듈에 번들된 템플릿
    default = Path(__file__).parent / "templates" / "ri" / "_template"
    if not default.exists():
        raise FileNotFoundError(
            f"RI 템플릿을 찾을 수 없음. RI_TEMPLATE_DIR 환경변수 지정 또는 "
            f"{default} 에 8 stage md 파일 배치 필요."
        )
    return default


def _render(text: str, ph: dict[str, str]) -> str:
    out = text
    for k, v in ph.items():
        out = out.replace(f"{{{{{k}}}}}", str(v))
    return out


def scaffold_ri(
    ctx: RIContext,
    *,
    project_dir: str | Path,
    force: bool = False,
) -> Path:
    """
    `<project_dir>/09-bridge/dev/ri/<JIRA_KEY>/` 아래 8 단 파일 생성.

    반환: 생성된 폴더 Path. 이미 존재하고 force=False 면 skip (로그 없이 그대로 반환).
    """
    pd = Path(project_dir)
    target = pd / "09-bridge" / "dev" / "ri" / ctx.jira_key
    if target.exists() and not force:
        return target

    target.mkdir(parents=True, exist_ok=True)
    template_dir = resolve_template_dir(pd)
    ph = ctx.placeholders()

    # 00-jira-meta.md (자동)
    meta_lines = [
        f"# Jira 메타 — {ctx.jira_key}", "",
        f"> 자동 생성: {datetime.now().isoformat(timespec='seconds')}", "",
        "| 항목 | 값 |", "|------|-----|",
        f"| JIRA_KEY | {ctx.jira_key} |",
        f"| SUMMARY | {ctx.summary} |",
        f"| ASSIGNEE | {ctx.assignee} |",
        f"| DUE_DATE | {ctx.due_date} |",
        f"| EPIC_KEY | {ctx.epic_key} |",
        f"| EPIC_SUMMARY | {ctx.epic_summary} |",
        f"| PHASE | {ctx.phase} |",
        f"| FIX_VERSION | {ctx.fix_version} |",
        f"| PRIORITY | {ctx.priority} |",
        f"| LABELS | {ctx.labels} |", "",
        f"**Jira 링크**: {ctx.jira_url}",
    ]
    (target / "00-jira-meta.md").write_text("\n".join(meta_lines) + "\n", encoding="utf-8")

    # 01~08 stage 파일 — 템플릿 복제 + 치환
    for name in STAGE_ORDER:
        src = template_dir / name
        if not src.exists():
            # 템플릿에 해당 단계 없으면 빈 skeleton 생성 (완전성 우선)
            (target / name).write_text(
                f"# {name} — {ctx.jira_key}\n\n(템플릿 없음 — 수동 작성 필요)\n",
                encoding="utf-8",
            )
            continue
        rendered = _render(src.read_text(encoding="utf-8"), ph)
        (target / name).write_text(rendered, encoding="utf-8")

    # hwpx 양식 등 참고 자료가 _template 에 있으면 복사
    for extra in template_dir.glob("*"):
        if extra.suffix.lower() in {".hwpx", ".hwp", ".docx"}:
            shutil.copy2(extra, target / extra.name)

    return target


def scaffold_many(
    contexts: list[RIContext],
    *,
    project_dir: str | Path,
    force: bool = False,
) -> tuple[int, int]:
    """여러 Task 에 대해 일괄 scaffolding. 반환: (생성, 스킵)."""
    created, skipped = 0, 0
    for ctx in contexts:
        before = (Path(project_dir) / "09-bridge" / "dev" / "ri" / ctx.jira_key).exists()
        scaffold_ri(ctx, project_dir=project_dir, force=force)
        if before and not force:
            skipped += 1
        else:
            created += 1
    return created, skipped


def rename_folders_to_jira_keys(
    project_dir: str | Path,
    *,
    ext_to_key: dict[str, str],
) -> list[tuple[str, str]]:
    """
    1차 External ID 로 생성된 폴더들을 Task import 후 실제 Jira Key 로 rename.
    반환: (from, to) 쌍 리스트.
    """
    base = Path(project_dir) / "09-bridge" / "dev" / "ri"
    renamed: list[tuple[str, str]] = []
    if not base.exists():
        return renamed
    for ext, key in ext_to_key.items():
        if ext == key:
            continue
        src = base / ext
        dst = base / key
        if src.exists() and not dst.exists():
            src.rename(dst)
            renamed.append((ext, key))
    return renamed


__all__ = [
    "RIContext",
    "STAGE_ORDER",
    "resolve_template_dir",
    "scaffold_ri",
    "scaffold_many",
    "rename_folders_to_jira_keys",
]
