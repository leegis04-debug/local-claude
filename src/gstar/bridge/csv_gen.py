"""
Jira Cloud CSV import 3-file 생성기 (award-to-dev 핵심).

입력: `BridgeConfig` + issue 모음 (Epic/Task/Sub-task 데이터 객체 리스트)
출력: `09-bridge/` 아래 3 개 CSV + README.md + gen 재실행 가능 상태.

Jira CSV 스키마 (15 컬럼 고정, UTF-8 with BOM, QUOTE_ALL):
  External ID, Issue Type, Summary, Description, Priority, Labels, Story Points,
  Assignee, Reporter, Epic Name, Epic Link, Parent, Due Date, Fix Version, Components

Parent 컬럼:
  - 1차 생성 시 Sub-task Parent 에는 Task 의 External ID (예: DT-T1.1.1) 를 넣는다.
  - 2차 Task import 완료 후 `refresh_subtasks(config, task_key_map)` 로 실제 Jira Key 로 치환.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from gstar.bridge.config import BridgeConfig


CSV_HEADER = [
    "External ID", "Issue Type", "Summary", "Description",
    "Priority", "Labels", "Story Points",
    "Assignee", "Reporter",
    "Epic Name", "Epic Link", "Parent",
    "Due Date", "Fix Version", "Components",
]


@dataclass
class EpicRow:
    ext_id: str
    summary: str
    description: str
    labels: list[str] = field(default_factory=list)
    priority: str = "High"
    epic_name: str = ""
    due_date: str = ""
    assignee: str = ""     # displayName; 빈값이면 PI
    reporter: str = ""     # displayName; 빈값이면 PI


@dataclass
class TaskRow:
    ext_id: str
    summary: str
    description: str
    epic_link: str                 # Epic External ID (DT-E1)
    priority: str = "High"
    labels: list[str] = field(default_factory=list)
    story_points: int | None = None
    assignee: str = ""
    reporter: str = ""
    due_date: str = ""
    fix_version: str = ""
    component: str = ""


@dataclass
class SubtaskRow:
    ext_id: str
    summary: str
    description: str
    parent_ref: str                # 1차: Task External ID (DT-T1.1.1). refresh_subtasks 후: Jira Key (DT-83).
    priority: str = "Medium"
    labels: list[str] = field(default_factory=list)
    story_points: int | None = None
    assignee: str = ""
    reporter: str = ""
    due_date: str = ""
    fix_version: str = ""
    component: str = ""


def _row_for_epic(cfg: BridgeConfig, e: EpicRow) -> list[str]:
    assignee = e.assignee or cfg.pi.display_name
    reporter = e.reporter or cfg.pi.display_name
    return [
        e.ext_id, "Epic", e.summary, e.description,
        e.priority, ",".join(e.labels), "",
        assignee, reporter,
        e.epic_name or e.summary, "", "",
        e.due_date, "", "",
    ]


def _row_for_task(cfg: BridgeConfig, t: TaskRow) -> list[str]:
    assignee = t.assignee or cfg.pi.display_name
    reporter = t.reporter or cfg.pi.display_name
    sp = "" if t.story_points is None else str(t.story_points)
    return [
        t.ext_id, "Task", t.summary, t.description,
        t.priority, ",".join(t.labels), sp,
        assignee, reporter,
        "", t.epic_link, "",
        t.due_date, t.fix_version, t.component,
    ]


def _row_for_subtask(cfg: BridgeConfig, s: SubtaskRow) -> list[str]:
    assignee = s.assignee or cfg.pi.display_name
    reporter = s.reporter or cfg.pi.display_name
    sp = "" if s.story_points is None else str(s.story_points)
    return [
        s.ext_id, "Sub-task", s.summary, s.description,
        s.priority, ",".join(s.labels), sp,
        assignee, reporter,
        "", "", s.parent_ref,
        s.due_date, s.fix_version, s.component,
    ]


def _write_csv(path: Path, rows: Iterable[list[str]]) -> int:
    # UTF-8 BOM + QUOTE_ALL — Jira Cloud CSV import 의 안정 조합.
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(CSV_HEADER)
        for r in rows:
            w.writerow(r)
            count += 1
    return count


def generate_csvs(
    cfg: BridgeConfig,
    *,
    epics: list[EpicRow],
    tasks: list[TaskRow],
    subtasks: list[SubtaskRow],
    out_dir: str | Path,
) -> dict[str, int]:
    """3-file CSV 생성. 반환: {filename: rowcount}."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    n_epics = _write_csv(out / "jira-import-1-epics.csv",
                         (_row_for_epic(cfg, e) for e in epics))
    n_tasks = _write_csv(out / "jira-import-2-tasks.csv",
                         (_row_for_task(cfg, t) for t in tasks))
    n_subtasks = _write_csv(out / "jira-import-3-subtasks.csv",
                            (_row_for_subtask(cfg, s) for s in subtasks))

    return {
        "jira-import-1-epics.csv": n_epics,
        "jira-import-2-tasks.csv": n_tasks,
        "jira-import-3-subtasks.csv": n_subtasks,
    }


def refresh_subtasks(
    cfg: BridgeConfig,
    *,
    subtasks: list[SubtaskRow],
    task_key_map: dict[str, str],      # {"DT-T1.1.1": "DT-83", ...}
    out_dir: str | Path,
) -> tuple[int, int]:
    """
    Task import 완료 후 실제 Jira Key 로 Parent 컬럼 치환.

    반환: (updated_rows, unmatched_rows) — unmatched > 0 이면 CSV 에 External ID 그대로 남김.
    """
    out = Path(out_dir)
    updated, unmatched = 0, 0
    rows: list[list[str]] = []
    for s in subtasks:
        new_parent = task_key_map.get(s.parent_ref, s.parent_ref)
        if new_parent != s.parent_ref:
            updated += 1
        else:
            unmatched += 1
        rows.append(_row_for_subtask(
            cfg,
            SubtaskRow(
                ext_id=s.ext_id, summary=s.summary, description=s.description,
                parent_ref=new_parent, priority=s.priority, labels=s.labels,
                story_points=s.story_points, assignee=s.assignee, reporter=s.reporter,
                due_date=s.due_date, fix_version=s.fix_version, component=s.component,
            ),
        ))
    _write_csv(out / "jira-import-3-subtasks.csv", rows)
    return (updated, unmatched)


def write_import_readme(cfg: BridgeConfig, out_dir: str | Path,
                        csv_counts: dict[str, int]) -> Path:
    """Jira 사이트 담당자용 import 절차 README."""
    out = Path(out_dir)
    p = out / "jira-import-README.md"
    def _fv_line(fv) -> str:
        if fv.start and fv.due:
            dates = f"start {fv.start} / release {fv.due}"
        elif fv.due:
            dates = f"release {fv.due}"
        else:
            dates = "dates TBD"
        return f"- `{fv.id}` — {fv.label} ({dates})"
    fvs = "\n".join(_fv_line(fv) for fv in cfg.fix_versions)
    components = "\n".join(f"- `{c}`" for c in cfg.components)
    members = "\n".join(f"- `{m.display_name}` ({m.email})" for m in [cfg.pi] + cfg.members)

    p.write_text(f"""# Jira 가져오기 가이드 — {cfg.project_name}

> 파일: `jira-import-1-epics.csv` / `jira-import-2-tasks.csv` / `jira-import-3-subtasks.csv`
> 대상: Jira Cloud **External System Import > CSV**
> 프로젝트 키: **{cfg.project_key}**
> 사이트: https://{cfg.jira_site}
> Row 수: Epic {csv_counts.get('jira-import-1-epics.csv', 0)} / Task {csv_counts.get('jira-import-2-tasks.csv', 0)} / Sub-task {csv_counts.get('jira-import-3-subtasks.csv', 0)}

## 1. 사전 준비 (Jira 측)

1. Jira 프로젝트 생성 — 키 **`{cfg.project_key}`** (이미 생성되어 있어야 함)
2. Issue Type 활성화: `Epic`, `Task`, `Sub-task`
3. 팀원 Jira 계정:
{members}
4. **Fix Versions + Components 자동 생성** — `bin/bridge provision`
   ```bash
   source ~/.config/<slug>/env.sh      # JIRA_SITE / JIRA_EMAIL / JIRA_TOKEN / JIRA_PROJECT
   bin/bridge provision --dry-run      # 생성 대상 미리보기
   bin/bridge provision                # 실제 생성. 이미 존재하는 이름은 스킵 (멱등)
   # 옵션: --versions-only / --components-only
   ```
   `bridge.yaml` 의 `fix_versions:` (id·label·start·due·gate) 와 `components:` 가 단일 진실. 수정은 yaml 만, 명령은 다시 돌리면 멱등 적용. 수동 fallback: Project settings → Releases / Components.

   Fix Versions:
{fvs}

   Components:
{components}
5. Custom Fields (선택): Story Points 기본 제공. IoU·Hypothesis 등 필요시 추가.

## 2. Import 순서 (3 단계 반드시 순서대로)

### 2-1. Epic import
1. ⚙ → System → External System Import → CSV
2. `jira-import-1-epics.csv` 업로드
3. Project: **{cfg.project_key}**
4. Field Mapping 확인 후 Begin Import
5. 완료 후 Jira 에서 Epic 실제 Key 확인 (예: {cfg.project_key}-NN)

### 2-2. Task import
1. `jira-import-2-tasks.csv` 업로드
2. `Epic Link` 는 External ID (예: `{cfg.project_key}-E1`) 그대로 — Wizard 가 1차 import Epic 과 자동 매칭
3. Begin Import

### 2-3. Sub-task import — **Parent 재생성 필수**
1. 먼저 로컬에서:
```bash
bash bin/bridge refresh-subtasks --project-dir "$PROJECT_DIR"
# → Jira API 호출해 Task External ID → Jira Key 매핑 후 3-subtasks.csv 갱신
```
2. `jira-import-3-subtasks.csv` 업로드 → Begin Import

## 3. 자주 발생하는 오류
| 증상 | 원인 | 해결 |
|-----|-----|-----|
| Epic Link 매칭 실패 | 1차 Epic import 전에 2차 올림 | 순서대로 재실행 |
| Sub-task Parent 매칭 실패 | Parent 가 External ID 그대로 | `bin/bridge refresh-subtasks` 실행 후 재업로드 |
| Assignee 매칭 실패 | 이름 vs 이메일 mismatch | Wizard 3단계 수동 매핑 |
| Due Date 파싱 실패 | 사이트 date format | Jira 설정 → YYYY-MM-DD |

## 4. Import 후 운영
- Scrum Board 생성 · Sprint 구성
- Custom Field 대시보드 (IoU·F1 라인 차트 등)
- Jira Automation: Sub-task 전부 Done → Parent In Review 등
- Slack 연동: `bin/bridge sync` (자동화 스크립트)

## 5. 재생성 (Task 추가/삭제 시)
1. `09-bridge/jira-breakdown.md` 갱신
2. `bin/bridge csv` → CSV 3개 덮어쓰기
3. 기존 Jira 티켓은 External ID 충돌로 skip — 신규만 delta import

## 6. 대안 — REST API 직접
조직 보안상 CSV import 불가 시:
- `POST /rest/api/3/issue/bulk` (최대 50/call)
- Epic → Task → Sub-task 3 회 호출로 분할
""", encoding="utf-8")
    return p


__all__ = [
    "CSV_HEADER",
    "EpicRow", "TaskRow", "SubtaskRow",
    "generate_csvs", "refresh_subtasks", "write_import_readme",
]
