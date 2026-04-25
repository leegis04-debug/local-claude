# ops + bridge 통합 가이드

> **범위**: `local-claude/ops/` (프로젝트 스캐폴딩·배포) + `local-claude/src/gstar/bridge/` (Jira 브리지)
> **최초 작성**: 2026-04-24 — `ai/ops/` 제거 + deep-tect 09-bridge 스크립트 일반화 후.

## 1. 재구성 배경

### 1-1. 이전 문제
- `ai/ops/project.sh` 가 `~/workspace/ai/` 트리에 하드코딩 (BASE_DIR 상위 2단계) → 다른 위치에서 재사용 불가
- deep-tect `09-bridge/` 에 프로젝트 산출물(CSV·jira-breakdown.md) + 재사용 스크립트(gen-jira-csv-v2.py·ri-gen.py·automation.py) + 디버그 코드 혼재 → 새 프로젝트 생성 시 복붙 필요
- Jira 토큰·프로젝트 키가 스크립트에 하드코딩 (`DT` 전제) → 다른 프로젝트 불가

### 1-2. 이후 구조 (2026-04-24)
```
local-claude/                                ← 재사용 도구 (프로젝트 불문)
├── ops/                                     # project.sh · 배포 스크립트 · 템플릿
│   ├── project.sh                           # 프로젝트 스캐폴딩 (ai/ops 에서 이전, 경로 유연화)
│   ├── new-project.sh · deploy.sh · squad-run.sh
│   ├── CLAUDE.md.template · opencode.json.template · upgrade-plan.template.md
│   ├── mcp.json.template                    # (신규) .mcp.json 복사용
│   ├── templates/                           # devops/ · mlops/ · idea-canvas.md · …
│   ├── form/                                # hwpx_fill.py 세트
│   └── squad-patterns/                      # 4 개 .md
├── src/gstar/bridge/                        # Jira 브리지 Python 모듈
│   ├── config.py                            # bridge.yaml 로더 (project key · 팀 · Fix Version)
│   ├── ids.py                               # ID 네임스페이스 동적 생성 (project.key 기반)
│   ├── description.py                       # Epic/Task/Sub-task Description 템플릿
│   ├── csv_gen.py                           # 3-file CSV 생성 + refresh_subtasks
│   ├── ri_scaffold.py                       # dev/ri/<KEY>/ 8 단 폴더
│   ├── jira_client.py                       # Jira REST v3 래퍼
│   ├── cli.py                               # bin/bridge 진입점
│   └── templates/ri/_template/              # 8 단 RI 템플릿 (deep-tect 에서 복사)
├── bin/bridge                               # python -m gstar.bridge.cli 래퍼
└── docs/ops-bridge.md                       # (이 문서)

<프로젝트>/09-bridge/                         ← 프로젝트 산출물만 (설정 + 생성물)
├── bridge.yaml                              # project.key · 팀 · Fix Version · Epic 정의
├── plan.json                                # Claude award-to-dev skill 이 생성한 계층 설계
├── jira-import-1-epics.csv                  # bin/bridge csv 산출
├── jira-import-2-tasks.csv
├── jira-import-3-subtasks.csv
├── jira-import-README.md                    # bin/bridge csv 가 자동 생성
├── jira-breakdown.md                        # 사람용 계층 요약 (Claude 작성)
├── task-keys.json                           # bin/bridge refresh-subtasks 산출 (Task 매핑)
├── dev/ri/<JIRA-KEY>/                       # bin/bridge ri-init 산출 (8 단 RI)
└── _versions/                               # plan.json 버전 백업

~/.config/<project-slug>/env.sh              ← Jira 자격 (chmod 600, 프로젝트별 분리)
```

## 2. 사용 흐름

### 2-1. 신규 프로젝트 생성

```
사용자:  cd "/Users/ljw0904/Desktop/Doc/Dagyeom Inc/projects"
사용자:  claude
(Claude Code)
사용자:  /asst:project deep-tect jw
```

내부 동작:
1. skill 이 `$PWD` upward 탐색해 projects 루트 결정
2. `~/.config/deep-tect/env.sh` 존재·토큰 검증. 없으면 대화식으로 Jira 정보 수집 → 파일 생성 → TextEdit 로 열기 → "yes" 대기 → 재검증
3. `bash local-claude/ops/project.sh new deep-tect jw` 실행 → 00-input ~ 09-bridge · dev/ · .mcp.json · CLAUDE.md 생성
4. `bin/bridge init --project-key DT --project-name Deep-Tect` 실행 → `09-bridge/bridge.yaml` skeleton 생성
5. bridge.yaml 편집기로 열기 → 사용자가 팀·Fix Version·Epic 채움
6. 다음 단계 안내 (`/jw:idea`)

### 2-2. 수주 후 Jira 계획 세우기

```
사용자:  (07-proposal/proposal-final.md 작성 완료 후)
사용자:  cd <프로젝트 폴더>
사용자:  /jw:award-to-dev
```

Claude 가 수행:
1. proposal-final.md · tech-spec.md · experiment-protocol.md 읽기
2. bridge.yaml 의 project.key·Fix Version·Epic 로드
3. Epic / Task / Sub-task 계층 설계 + Description 8섹션 상세 작성
4. `09-bridge/plan.json` 저장
5. `09-bridge/jira-breakdown.md` 사람용 요약 작성
6. 후속 액션 안내 (`/asst:bridge csv` 등)

### 2-3. CSV 생성 → Jira import → RI scaffolding

```
/asst:bridge csv               # plan.json → 3-file CSV + README
(사용자가 Jira 웹에서 Epic CSV 업로드 → 성공)
(Task CSV 업로드 → 성공)
/asst:bridge refresh           # Jira API 로 Task External→Key 매핑 → subtasks CSV 갱신
(사용자가 Sub-task CSV 업로드 → 성공)
/asst:bridge ri-init           # 각 Task 에 dev/ri/<ext-id>/ 8 단 RI 생성 (External ID 기준)
/asst:bridge rename            # RI 폴더 External ID → Jira Key 로 rename
```

## 3. `ops/project.sh` 변경점 (ai/ 이전)

### 기존 (ai/ops/)
```bash
BASE_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"       # ai/ 전제
PROJECTS_DIR="${PROJECTS_DIR:-$BASE_DIR/projects}"     # ai/projects 강제
...
ln -s ../../../claude/ops/.claude/agents .claude/agents  # dead 경로
ln -s ../../.mcp.json .mcp.json                          # ai/.mcp.json 의존
```

### 이후 (local-claude/ops/)
```bash
LOCAL_CLAUDE_HOME="${LOCAL_CLAUDE_HOME:-$(dirname "$SCRIPT_DIR")}"

resolve_projects_dir() {
  [ -n "${PROJECTS_DIR:-}" ] && { echo "$PROJECTS_DIR"; return; }
  d="$PWD"; while [ "$d" != "/" ]; do
    [ -f "$d/_index.md" ] && { echo "$d"; return; }
    d="$(dirname "$d")"
  done
  echo "$PWD"        # 첫 프로젝트 루트
}
PROJECTS_DIR="$(resolve_projects_dir)"
...
# agents 심링크 drop (dead 경로였음)
# .mcp.json 은 local-claude/ops/mcp.json.template 복사 (심링크 아님)
```

## 4. `bridge.yaml` 스키마

모든 프로젝트별 Jira 설정의 SSoT. `bin/bridge init` 이 skeleton 을 만들고 사용자가 채움.

```yaml
project:
  key: DT                          # Jira 프로젝트 키 — 모든 ID prefix
  name: Deep-Tect
  slug: deep-tect
  site: lee35460.atlassian.net

team:
  pi:
    key: PI
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
  # ...

components:
  - "Phase 1.1"
  - "Phase 1.2"
  # ...

labels:
  domain: [vision, language, reasoning]
  kind: [paper, patent, infra, pm, research-skill, tracker, hypothesis]
  tech: [sam2, clip, vlm, self-h100, kait-h100, rtx5060]

epics:
  - ext_id: DT-E1
    summary: "Vision Model — Region-level 이상 분리"
    labels: [vision, sam2]
    priority: Highest
    epic_name: "Vision 1단계"

custom_fields:
  story_points: true
  hypothesis: [H1, H2, H3, H4, H5]
  # ...
```

## 5. `plan.json` 스키마

Claude `/jw:award-to-dev` 또는 `/re:award-to-dev` 가 생성. `bin/bridge csv` 가 읽어 3-file CSV 로 변환.

```json
{
  "epics":    [{"ext_id":"DT-E1", "summary":"...", "description":"...", "labels":[...], "priority":"Highest", "epic_name":"...", "due_date":"2026-12-31"}],
  "tasks":    [{"ext_id":"DT-T1.1.1", "summary":"...", "description":"...", "epic_link":"DT-E1", "labels":[...], "story_points":3, "assignee":"송주한", "due_date":"...", "fix_version":"M1-Q2-26", "component":"Phase 1.1"}],
  "subtasks": [{"ext_id":"DT-ST1.1.1a", "summary":"...", "description":"...", "parent_ref":"DT-T1.1.1", "story_points":1, "assignee":"송주한", "due_date":"...", "fix_version":"M1-Q2-26", "component":"Phase 1.1"}],
  "metadata": {"version":"v1","track":"jw","source_docs":[...],"generated_at":"..."}
}
```

## 6. Jira CSV import 3-단계 (실사례)

Jira Cloud 는 단일 CSV 로 Epic+Task+Sub-task 를 올리면 External ID / Parent 순환 참조로 실패.
**순차 3-file import** 만 안정적:

```
1차: jira-import-1-epics.csv       → Jira 가 Epic 에 실제 Key 할당 (DT-E1 → DT-76 등)
2차: jira-import-2-tasks.csv       → Epic Link 는 External ID (DT-E1) 그대로. Wizard 가 자동 매칭
-- 여기서 `bin/bridge refresh-subtasks` 실행 → Task External→Key 매핑 수집 후 subtasks CSV 갱신
3차: jira-import-3-subtasks.csv    → Parent 는 실제 Task Key (DT-83 등)
```

deep-tect 케이스에서 파일명에 `-fixed` 가 붙은 이유가 이 3-file 순서 (Sub-task 재생성).

## 7. ID 네임스페이스 (project.key 동적)

`bridge.yaml` 의 `project.key` 가 모든 ID 의 prefix. 예: key=`DT`:

| 종류 | 패턴 | 예 |
|------|------|-----|
| Epic | `{K}-E{n}` | DT-E1 |
| 일반 Task | `{K}-T{phase}.{seq}` | DT-T1.1.1 |
| 분할 (특허) | `{K}-T{phase}.{seq}{a\|b}` | DT-T1.3.3a |
| Paper Task | `{K}-TP{n}.{seq}` | DT-TP1.1 |
| 인프라 Task | `{K}-TInfra.{seq}` | DT-TInfra.1 |
| 연구역량 Task | `{K}-TR.{seq}{a\|b\|c}` | DT-TR.4a |
| PM Task | `{K}-TPM.{seq}{a\|b\|c\|d}` | DT-TPM.1a |
| 월별 PM Task | `{K}-TPM.{seq}-{month}` | DT-TPM.3-jun |
| Sub-task | `{K}-ST{phase}.{seq}{letter}` | DT-ST1.1.1a |

`src/gstar/bridge/ids.py` 가 helper 함수 제공 (프로젝트마다 다른 키 자동 적용).

## 8. deep-tect 기존 스크립트 처분

**결정: 보존**. 역사·재현·자동화 동작 중 (automation.py 의 Google Calendar sync 가 cron 에서 실행 중).

향후:
- 새 프로젝트: `local-claude/bin/bridge` 만 사용
- deep-tect: 기존 스크립트 그대로. 업데이트 필요 시 local-claude 로 마이그레이션 별도 결정

## 9. 확인 체크리스트 (신규 프로젝트 Jira 완전 전환)

- [ ] `~/.config/<slug>/env.sh` 생성 + 토큰 입력 + `chmod 600`
- [ ] `/asst:bridge check` 통과 (인증·프로젝트 접근)
- [ ] `09-bridge/bridge.yaml` 에 team·fix_versions·components·epics 채움
- [ ] Jira 웹에서 Fix Versions·Components 사전 생성
- [ ] `/jw:award-to-dev` (또는 `/re:`) 로 `plan.json` 생성
- [ ] `/asst:bridge csv` → CSV 3 개 생성
- [ ] 1차 Epic CSV 업로드 성공
- [ ] 2차 Task CSV 업로드 성공
- [ ] `/asst:bridge refresh` → task-keys.json 생성, subtasks CSV 갱신
- [ ] 3차 Sub-task CSV 업로드 성공
- [ ] `/asst:bridge ri-init` → dev/ri/ 생성
- [ ] `/asst:bridge rename` → RI 폴더 Jira Key 로 변환

## 10. 트러블슈팅

| 증상 | 원인 | 해결 |
|-----|-----|-----|
| `bin/bridge` 실행 시 `ModuleNotFoundError: gstar` | PYTHONPATH 미설정 | bin/bridge 가 자동 설정함. 직접 python 호출 시 `PYTHONPATH=<lc>/src` |
| `bridge.yaml` 누락 에러 | `bin/bridge init` 미수행 | `/asst:bridge init` 또는 `bin/bridge --project-dir <d> init` |
| Jira 401 | 토큰 만료·재생성 필요 | https://id.atlassian.com/manage-profile/security/api-tokens → 재발급 → env.sh 갱신 |
| Sub-task Parent 매칭 실패 | External ID 그대로 업로드 | `/asst:bridge refresh` 먼저 실행 |
| `customfield_10100` 매칭 실패 | Jira External ID custom field ID 다름 | `bin/bridge refresh-subtasks --ext-id-field <id>` |
| RI 폴더가 External ID 로만 있음 | Task import 후 rename 미수행 | `/asst:bridge rename` |
