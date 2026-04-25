#!/bin/bash
# 프로젝트 관리 스크립트
# 사용법:
#   bash project.sh new <이름> [jw|re]  — 새 프로젝트 생성 (트랙: jw=수주문서, re=연구개발)
#   bash project.sh init-dev <이름>     — dev/ 환경 스캐폴딩 (jw→DevOps, re→MLOps)
#   bash project.sh delete <이름>       — 프로젝트 삭제
#   bash project.sh list                — 프로젝트 목록
#   bash project.sh status              — 전체 프로젝트 상태
#
# 워크플로우:
#   A. 수주 문서 트랙 (jw)     B. 연구개발 문서 트랙 (re)
#     /jw:idea                   /re:idea
#     → /jw:debate               → /re:debate
#     → /jw:structure            → /re:structure
#     → /jw:spec                 → /re:spec
#     → /jw:risk-check           → /re:risk-check
#     → /jw:experiment-plan      → /re:experiment-plan
#     → /jw:proposal             → /re:proposal
#               ↓                          ↓
#   C. 공통 브리지: /jw:award-to-dev → Epic/Milestone/Task 변환
#               ↓
#   D. 공통 개발 실행: /gsd-new-project → ... → /gsd-ship

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# LOCAL_CLAUDE_HOME: 이 스크립트가 위치한 local-claude 루트 (ops/ 의 parent).
# ai/ 구조 이전 규약 (BASE_DIR=$SCRIPT_DIR/../..) 은 폐기. 이제 LOCAL_CLAUDE_HOME 이 master.
LOCAL_CLAUDE_HOME="${LOCAL_CLAUDE_HOME:-$(dirname "$SCRIPT_DIR")}"

# PROJECTS_DIR 해석 우선순위:
#   1. env PROJECTS_DIR 명시
#   2. upward 탐색: $PWD 부터 위로 올라가며 _index.md 찾기 (projects 루트 마커)
#   3. $PWD 에 _index.md 없으면 $PWD 자체가 projects 루트라고 간주 (첫 프로젝트 생성)
resolve_projects_dir() {
  if [ -n "${PROJECTS_DIR:-}" ]; then
    echo "$PROJECTS_DIR"; return 0
  fi
  local d="$PWD"
  while [ "$d" != "/" ] && [ -n "$d" ]; do
    if [ -f "$d/_index.md" ]; then
      echo "$d"; return 0
    fi
    d="$(dirname "$d")"
  done
  # 폴백: $PWD 자체 (아직 _index.md 없는 첫 프로젝트 루트)
  echo "$PWD"
}

PROJECTS_DIR="$(resolve_projects_dir)"
INDEX="$PROJECTS_DIR/_index.md"

CMD="$1"
PROJECT_NAME="$2"
TRACK="$3"  # jw 또는 re

show_help() {
  echo "프로젝트 관리 스크립트"
  echo ""
  echo "사용법:"
  echo "  bash project.sh new <이름> [jw|re]  새 프로젝트 생성 (기본: jw)"
  echo "  bash project.sh init-dev <이름>     dev/ 환경 스캐폴딩 (jw→DevOps, re→MLOps)"
  echo "  bash project.sh sync <이름> [push|pull|run|data]  NAS 경유 맥북↔회사PC dev/ 동기화"
  echo "  bash project.sh deploy <이름> [build|push|pull|run]  Docker Hub 배포 + 원격 실행"
  echo "  bash project.sh backflow <이름>     실험 결과 → spec/proposal 수치 역류 (re)"
  echo "  bash project.sh deploy-done <이름>  배포 완료 보고 → Jira 업데이트 (jw)"
  echo "  bash project.sh delete <이름>       프로젝트 삭제"
  echo "  bash project.sh list                프로젝트 목록"
  echo "  bash project.sh status              전체 상태 보기"
  echo ""
  echo "트랙:"
  echo "  jw  수주 문서 트랙 (외부 제출 문서 + Jira 기록)"
  echo "  re  연구개발 문서 트랙 (연구 수행 문서 + 실험/기록)"
  echo ""
  echo "예시:"
  echo "  bash project.sh new smart-factory        # jw 트랙 (기본)"
  echo "  bash project.sh new ai-research re       # re 트랙"
  echo "  bash project.sh delete pilot"
}

# === NEW ===
cmd_new() {
  if [ -z "$PROJECT_NAME" ]; then
    echo "오류: 프로젝트명을 입력하세요."
    echo "사용법: bash project.sh new <이름> [jw|re]"
    exit 1
  fi

  # 트랙 기본값: jw
  if [ -z "$TRACK" ]; then
    TRACK="jw"
  fi

  if [ "$TRACK" != "jw" ] && [ "$TRACK" != "re" ]; then
    echo "오류: 트랙은 jw 또는 re만 가능합니다."
    echo "  jw = 수주 문서 트랙"
    echo "  re = 연구개발 문서 트랙"
    exit 1
  fi

  PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"

  if [ -d "$PROJECT_DIR" ]; then
    echo "오류: $PROJECT_NAME 이미 존재합니다."
    exit 1
  fi

  if [ "$TRACK" = "jw" ]; then
    TRACK_LABEL="수주 문서"
    TRACK_PREFIX="/jw"
  else
    TRACK_LABEL="연구개발 문서"
    TRACK_PREFIX="/re"
  fi

  echo "=== 프로젝트 생성: $PROJECT_NAME ($TRACK_LABEL 트랙) ==="

  mkdir -p "$PROJECT_DIR"
  cd "$PROJECT_DIR"

  git init

  # --- A/B. 문서 트랙 8단계 (jw 또는 re) ---
  mkdir -p 00-input/BASE 00-input/REF 00-input/ING \
           01-idea/_wip \
           02-debate/_wip \
           03-structure/_wip \
           04-spec/_wip \
           05-risk-check/_wip \
           06-experiment-plan/_wip \
           07-proposal/_wip 07-proposal/_archive 07-proposal/_review

  # --- C. 공통 브리지 (award-to-dev 변환 산출물) ---
  mkdir -p 09-bridge

  # --- D. 공통 개발 실행 (GSD workspace) ---
  mkdir -p dev

  mkdir -p .claude

  # Agents: 기존 ai/claude/ops/.claude/agents 심링크는 dead 경로였음 — drop.
  # 향후 local-claude 에 공용 agents 가 추가되면 여기서 link.
  # 예: [ -d "$LOCAL_CLAUDE_HOME/.claude/agents" ] && ln -sfn "$LOCAL_CLAUDE_HOME/.claude/agents" .claude/agents

  # .mcp.json — local-claude/ops/mcp.json.template 복사 (심링크 아님 — 프로젝트 독립)
  if [ -f "$SCRIPT_DIR/mcp.json.template" ]; then
    cp "$SCRIPT_DIR/mcp.json.template" .mcp.json
  fi

  cp "$SCRIPT_DIR/CLAUDE.md.template" CLAUDE.md
  cp "$SCRIPT_DIR/opencode.json.template" opencode.json

  # status.md에 트랙 정보 포함
  cat > status.md << EOF
# $PROJECT_NAME
- 트랙: $TRACK ($TRACK_LABEL)
- 현재 단계: 01-idea
- 진행률: 0%
- 다음 액션: $TRACK_PREFIX:idea 실행
- 생성일: $(date +%Y-%m-%d)
EOF

  cat > .gitignore << EOF
.DS_Store
*.tmp
.mcp.json
EOF

  git add .
  git commit -m "init: $PROJECT_NAME ($TRACK 트랙) 운영체계 구조"

  # _index.md 업데이트
  echo "| $PROJECT_NAME | $TRACK | 01-idea | 0% | $(date +%Y-%m-%d) |" >> "$INDEX"

  # ── 비서 OS 통합 (ROADMAP Phase 2-4) ──────────────────────────────────

  GATEWAY="http://100.79.251.53:8000"
  ASST_TOKEN="${ASST_TOKEN:-}"
  if [ -f "$HOME/.config/asst/config" ]; then
    source "$HOME/.config/asst/config"
    ASST_TOKEN="${ASST_TOKEN:-}"
  fi

  if [ -n "$ASST_TOKEN" ]; then
    echo "[비서 OS] Gateway 연동 중..."

    # 1. 즉시 프로젝트 동기화 (15분 cron 대기 없이)
    curl -sf -X POST "$GATEWAY/state/projects/sync" \
      -H "X-Auth-Token: $ASST_TOKEN" \
      -H "Content-Type: application/json" >/dev/null 2>&1 \
      && echo "  ✓ 프로젝트 DB 동기화" || echo "  ✗ DB 동기화 실패 (무시)"

    # 2. 초기 태스크 등록 (기본 마일스톤)
    AXIS="bizplan"
    [ "$TRACK" = "re" ] && AXIS="rnd"
    curl -sf -X POST "$GATEWAY/tasks" \
      -H "X-Auth-Token: $ASST_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"project_slug\":\"$PROJECT_NAME\",\"title\":\"$TRACK_PREFIX:idea 실행\",\"axis\":\"$AXIS\",\"priority\":\"high\",\"source\":\"project.sh\"}" >/dev/null 2>&1 \
      && echo "  ✓ 초기 태스크 등록" || echo "  ✗ 태스크 등록 실패 (무시)"

    # 3. P-Reinforce 메모 (새 프로젝트 시작 기록)
    curl -sf -X POST "$GATEWAY/notes" \
      -H "X-Auth-Token: $ASST_TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"text\":\"[project.sh] 새 프로젝트 시작: $PROJECT_NAME, 트랙: $TRACK ($TRACK_LABEL)\",\"source\":\"cli\",\"tags\":[\"$TRACK\",\"new-project\",\"$PROJECT_NAME\"]}" >/dev/null 2>&1 \
      && echo "  ✓ P-Reinforce 메모 저장" || echo "  ✗ 메모 실패 (무시)"

    # 4. form-ref.json 자동 생성 (HWPX 양식이 00-input/BASE/에 있으면)
    HWPX_FILE=$(find 00-input/BASE/ -name "*.hwpx" -o -name "*.HWPX" 2>/dev/null | head -1)
    if [ -n "$HWPX_FILE" ] && [ "$TRACK" = "jw" ]; then
      # form_id는 프로젝트명을 기본으로 사용 (나중에 /jw:idea에서 정확한 양식 매핑)
      echo "{\"form_id\": \"$PROJECT_NAME\", \"hwpx_source\": \"$(basename "$HWPX_FILE")\"}" > 00-input/form-ref.json
      echo "  ✓ form-ref.json 생성 (양식: $(basename "$HWPX_FILE"))"
    fi

    echo "[비서 OS] 연동 완료"
  else
    echo "[비서 OS] ASST_TOKEN 미설정 — Gateway 연동 건너뜀 (asst setup 먼저 실행)"
  fi

  # ── 비서 OS 통합 끝 ────────────────────────────────────────────────────

  echo ""
  echo "=== 완료 ==="
  echo "경로: $PROJECT_DIR"
  echo "트랙: $TRACK ($TRACK_LABEL)"
  echo ""
  echo "  ┌──────┬──────────────────────────┬──────────────────────────────────────────┐"
  echo "  │ 단계 │          스킬            │                   설명                   │"
  echo "  ├──────┼──────────────────────────┼──────────────────────────────────────────┤"
  echo "  │  1   │ $TRACK_PREFIX:idea            │ 막연한 주제 → 문제-기회 정제             │"
  echo "  │  2   │ $TRACK_PREFIX:debate          │ 찬반·심사위원·현장 시점 다면 공격        │"
  echo "  │  3   │ $TRACK_PREFIX:structure       │ 토론 결과 → 양식 블록 프레임 변환        │"
  echo "  │  4   │ $TRACK_PREFIX:spec            │ 구조화 프레임 → 기술항목 매핑 명세서     │"
  echo "  │  5   │ $TRACK_PREFIX:risk-check      │ 과장·모호성·KPI 허점·양식 커버리지 검증  │"
  echo "  │  6   │ $TRACK_PREFIX:experiment-plan  │ 기술명세 검증 → 실증 실험 프로토콜       │"
  echo "  │  7   │ $TRACK_PREFIX:proposal        │ 기술명세 + 구조 프레임 → 사업계획서      │"
  echo "  ├──────┼──────────────────────────┼──────────────────────────────────────────┤"
  echo "  │  C   │ $TRACK_PREFIX:award-to-dev     │ 문서 → Epic/Task/Sub-task 계획 (plan.json)│"
  echo "  │  C' │ /asst:bridge csv         │ plan.json → Jira 3-file CSV              │"
  echo "  │  D   │ /gsd-*                   │ 개발 실행 (dev/ 디렉토리)                │"
  echo "  └──────┴──────────────────────────┴──────────────────────────────────────────┘"
  echo ""
  echo "다음:"
  echo "  1. CLAUDE.md에 프로젝트명, 사업유형, 수요기업 기입"
  echo "  2. 공고문/양식이 있으면 00-input/에 복사"
  echo "  3. $TRACK_PREFIX:idea 실행"
}

# === DELETE ===
cmd_delete() {
  if [ -z "$PROJECT_NAME" ]; then
    echo "오류: 삭제할 프로젝트명을 입력하세요."
    echo "사용법: bash project.sh delete <이름>"
    exit 1
  fi

  PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"

  if [ ! -d "$PROJECT_DIR" ]; then
    echo "오류: $PROJECT_NAME 프로젝트가 존재하지 않습니다."
    exit 1
  fi

  # 확인
  echo "=== 프로젝트 삭제: $PROJECT_NAME ==="
  echo "경로: $PROJECT_DIR"
  echo ""
  echo "포함된 파일:"
  find "$PROJECT_DIR" -not -path '*/.git/*' -type f | wc -l | xargs -I{} echo "  {} 개 파일"
  echo ""
  read -p "정말 삭제하시겠습니까? (y/N): " confirm

  if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "취소되었습니다."
    exit 0
  fi

  # 삭제
  rm -rf "$PROJECT_DIR"

  # _index.md에서 해당 라인 제거
  if [ -f "$INDEX" ]; then
    sed -i '' "/| $PROJECT_NAME /d" "$INDEX"
  fi

  echo "=== 삭제 완료: $PROJECT_NAME ==="
}

# === LIST ===
cmd_list() {
  echo "=== 프로젝트 목록 ==="
  echo ""

  # Gateway 데이터가 있으면 풍부한 정보 표시
  ASST_TOKEN="${ASST_TOKEN:-}"
  [ -f "$HOME/.config/asst/config" ] && source "$HOME/.config/asst/config"

  if [ -n "$ASST_TOKEN" ]; then
    gateway_data=$(curl -sf "http://100.79.251.53:8000/state/projects" -H "X-Auth-Token: $ASST_TOKEN" 2>/dev/null)
    if [ -n "$gateway_data" ]; then
      echo "$gateway_data" | python3 -c "
import sys,json
from datetime import datetime,timezone
d=json.load(sys.stdin)
now=datetime.now(timezone.utc)
print(f'  {\"Name\":<20} {\"Stage\":<14} {\"Axis\":<8} {\"Days\":<6} {\"Tasks\":<6}')
print('  '+'-'*56)
for p in d.get('projects',[]):
    stage=p.get('current_stage','—')[:13]
    days=''
    if p.get('last_activity'):
        try:
            la=datetime.fromisoformat(p['last_activity'].replace('Z','+00:00'))
            d_val=(now-la).days
            days=f'{d_val}d'
        except: pass
    print(f'  {p[\"slug\"]:<20} {stage:<14} {p.get(\"axis\",\"?\"):<8} {days:<6}')
" 2>/dev/null && return
    fi
  fi

  # Fallback: 로컬 status.md만
  for dir in "$PROJECTS_DIR"/*/; do
    name=$(basename "$dir")
    if [ "$name" != "*" ]; then
      if [ -f "$dir/status.md" ]; then
        track=$(grep "트랙" "$dir/status.md" | head -1 | sed 's/.*: //' | cut -d' ' -f1)
        stage=$(grep "현재 단계" "$dir/status.md" | head -1 | sed 's/.*: //')
        echo "  $name  [$track]  →  $stage"
      else
        echo "  $name  →  (status.md 없음)"
      fi
    fi
  done
}

# === STATUS ===
cmd_status() {
  echo "=== 프로젝트 상태 ==="
  echo ""
  for dir in "$PROJECTS_DIR"/*/; do
    name=$(basename "$dir")
    if [ "$name" != "*" ] && [ -f "$dir/status.md" ]; then
      echo "--- $name ---"
      cat "$dir/status.md"
      echo ""
    fi
  done
}

# === INIT-DEV (Phase 5 §5.4 — award-to-dev 브리지) ===
cmd_init_dev() {
  if [ -z "$PROJECT_NAME" ]; then
    echo "오류: 프로젝트명을 입력하세요."
    echo "사용법: bash project.sh init-dev <이름>"
    exit 1
  fi

  PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"

  if [ ! -d "$PROJECT_DIR" ]; then
    echo "오류: $PROJECT_NAME 프로젝트가 존재하지 않습니다."
    exit 1
  fi

  # 트랙 감지: CLI 인자($TRACK) 우선, 없으면 status.md에서 추출
  CLI_TRACK="$TRACK"
  if [ "$CLI_TRACK" != "jw" ] && [ "$CLI_TRACK" != "re" ]; then
    TRACK=$(grep "트랙:" "$PROJECT_DIR/status.md" 2>/dev/null | head -1 | awk '{print $NF}' | tr -d '()')
    if [ -z "$TRACK" ]; then
      TRACK=$(grep "^- 트랙:" "$PROJECT_DIR/status.md" 2>/dev/null | awk '{print $3}')
    fi
  fi

  if [ "$TRACK" != "jw" ] && [ "$TRACK" != "re" ]; then
    echo "오류: 트랙 감지 실패 (status.md에서 jw/re를 찾을 수 없음)"
    echo "직접 지정: bash project.sh init-dev <이름> [jw|re]"
    exit 1
  fi

  DEV_DIR="$PROJECT_DIR/dev"
  TEMPLATE_DIR="$SCRIPT_DIR/templates"

  echo "=== dev/ 환경 스캐폴딩: $PROJECT_NAME ($TRACK) ==="

  mkdir -p "$DEV_DIR/src" "$DEV_DIR/tests"

  if [ "$TRACK" = "re" ]; then
    # MLOps 환경 (연구/실험)
    echo "[MLOps] re 트랙 — 실험 추적 환경 구성"

    cp "$TEMPLATE_DIR/mlops/Makefile" "$DEV_DIR/Makefile"
    cp "$TEMPLATE_DIR/mlops/Dockerfile" "$DEV_DIR/Dockerfile"
    cp "$TEMPLATE_DIR/mlops/requirements.txt" "$DEV_DIR/requirements.txt"
    cp "$TEMPLATE_DIR/mlops/.pre-commit-config.yaml" "$DEV_DIR/.pre-commit-config.yaml"

    mkdir -p "$DEV_DIR/experiments" "$DEV_DIR/configs"

    # lab_tracker 설정 파일
    cat > "$DEV_DIR/configs/lab_config.yaml" << LABEOF
# lab_tracker 설정 — 프로젝트별 기본값
project: $PROJECT_NAME
nas_target: "\${LAB_TRACKER_NAS_TARGET}"
default_protocol_ref: "06-experiment-plan/experiment-protocol.md"
default_hypothesis_ref: "01-idea/research-canvas.md"
LABEOF

    # src/train.py 스켈레톤
    cat > "$DEV_DIR/src/train.py" << 'TRAINEOF'
"""학습 스크립트 — lab_tracker 자동 기록."""
import argparse
from lab_tracker import Experiment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="default")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--lr", type=float, default=1e-4)
    args = parser.parse_args()

    exp = Experiment(args.name)
    exp.log_params(vars(args))

    for epoch in range(args.epochs):
        # TODO: 실제 학습 로직
        train_loss = 1.0 - epoch * 0.05
        exp.log_metrics({"epoch": epoch, "train_loss": train_loss}, step=epoch)

    exp.finish(status="success")


if __name__ == "__main__":
    main()
TRAINEOF

    # src/eval.py 스켈레톤
    cat > "$DEV_DIR/src/eval.py" << 'EVALEOF'
"""평가 스크립트."""
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args()
    # TODO: 실제 평가 로직
    print(f"Evaluating checkpoint: {args.checkpoint}")


if __name__ == "__main__":
    main()
EVALEOF

    echo "  ✓ Makefile (train/eval/sync/docker 타겟)"
    echo "  ✓ Dockerfile (CUDA 12.0 + Python)"
    echo "  ✓ requirements.txt (torch, pytest, ruff)"
    echo "  ✓ .pre-commit-config.yaml (ruff + mypy)"
    echo "  ✓ experiments/ (lab_tracker 출력 디렉토리)"
    echo "  ✓ src/train.py, src/eval.py 스켈레톤"
    echo "  ✓ configs/lab_config.yaml"

  else
    # DevOps 환경 (솔루션 개발/배포)
    echo "[DevOps] jw 트랙 — CI/CD + 배포 환경 구성"

    cp "$TEMPLATE_DIR/devops/Makefile" "$DEV_DIR/Makefile"
    cp "$TEMPLATE_DIR/devops/Dockerfile" "$DEV_DIR/Dockerfile"
    cp "$TEMPLATE_DIR/devops/docker-compose.dev.yml" "$DEV_DIR/docker-compose.dev.yml"
    cp "$TEMPLATE_DIR/devops/requirements.txt" "$DEV_DIR/requirements.txt"
    cp "$TEMPLATE_DIR/devops/.pre-commit-config.yaml" "$DEV_DIR/.pre-commit-config.yaml"

    mkdir -p "$DEV_DIR/.github/workflows"

    # GitHub Actions CI
    cat > "$DEV_DIR/.github/workflows/ci.yml" << 'CIEOF'
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: ruff check src/ tests/
      - run: pytest tests/ -v --cov=src
CIEOF

    # src/main.py 스켈레톤
    cat > "$DEV_DIR/src/main.py" << 'MAINEOF'
"""FastAPI 메인 서버."""
from fastapi import FastAPI

app = FastAPI(title="SERVICE_NAME")


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
MAINEOF

    echo "  ✓ Makefile (dev/test/build/deploy-staging/deploy-prod 타겟)"
    echo "  ✓ Dockerfile + docker-compose.dev.yml"
    echo "  ✓ requirements.txt (fastapi, pytest, ruff)"
    echo "  ✓ .pre-commit-config.yaml (ruff + mypy + security)"
    echo "  ✓ .github/workflows/ci.yml (GitHub Actions)"
    echo "  ✓ src/main.py 스켈레톤"
  fi

  # 공통: .gitignore
  cat > "$DEV_DIR/.gitignore" << 'GIEOF'
.venv/
__pycache__/
*.pyc
.mypy_cache/
.ruff_cache/
.pytest_cache/
*.egg-info/
dist/
build/
experiments/*/artifacts/
*.pt
*.pth
*.onnx
.DS_Store
GIEOF

  # === Git 초기화 + NAS bare repo 설정 ===
  echo ""
  echo "  Git 초기화..."
  cd "$DEV_DIR"
  if [ ! -d ".git" ]; then
    git init > /dev/null 2>&1
    git add -A
    git commit -m "init: ${PROJECT_NAME} dev/ scaffold (${TRACK})" > /dev/null 2>&1
    echo "    ✓ git init + 초기 커밋"
  else
    echo "    ✓ git 이미 초기화됨"
  fi

  # NAS bare repo 생성 + remote 등록
  NAS_REPO="ljw-op:/mnt/nas/workspace/repos/${PROJECT_NAME}.git"
  if ! git remote get-url nas > /dev/null 2>&1; then
    echo "  NAS bare repo 설정..."
    ssh ljw-op "mkdir -p /mnt/nas/workspace/repos && \
      [ -d /mnt/nas/workspace/repos/${PROJECT_NAME}.git ] || \
      git init --bare /mnt/nas/workspace/repos/${PROJECT_NAME}.git" > /dev/null 2>&1

    if [ $? -eq 0 ]; then
      git remote add nas "$NAS_REPO"
      git push -u nas main > /dev/null 2>&1
      echo "    ✓ NAS remote: $NAS_REPO"
    else
      echo "    ⚠ NAS 연결 실패 (나중에 asst project sync로 설정 가능)"
    fi
  else
    echo "    ✓ NAS remote 이미 등록됨"
  fi
  cd - > /dev/null

  echo ""
  echo "=== dev/ 환경 준비 완료 ==="
  echo "경로: $DEV_DIR"
  echo ""
  echo "동기화 (맥북 중심 워크플로):"
  echo "  asst project sync $PROJECT_NAME clone    # 회사 PC에 최초 clone"
  echo "  asst project sync $PROJECT_NAME push     # 맥북 → NAS → 회사 PC"
  echo "  asst project sync $PROJECT_NAME pull     # 회사 PC → NAS → 맥북"
  if [ "$TRACK" = "re" ]; then
    echo ""
    echo "회사 PC에서 실험:"
    echo "  cd ~/projects/$PROJECT_NAME/dev"
    echo "  make setup && make train"
  else
    echo ""
    echo "회사 PC에서 개발:"
    echo "  cd ~/projects/$PROJECT_NAME/dev"
    echo "  make setup && make dev"
  fi
}

# === BACKFLOW (Phase 5 §5.4b — 실험 결과 → 문서 역류) ===
cmd_backflow() {
  if [ -z "$PROJECT_NAME" ]; then
    echo "오류: 프로젝트명을 입력하세요."
    echo "사용법: bash project.sh backflow <이름>"
    exit 1
  fi

  PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"
  if [ ! -d "$PROJECT_DIR" ]; then
    echo "오류: $PROJECT_NAME 프로젝트가 존재하지 않습니다."
    exit 1
  fi

  # 최신 comparison 파일 찾기
  NOTES_DIR="$PROJECT_DIR/research_notes"
  if [ ! -d "$NOTES_DIR" ]; then
    NOTES_DIR="$PROJECT_DIR/dev/research_notes"
  fi

  LATEST_COMP=$(ls -t "$NOTES_DIR"/comparison_*.md 2>/dev/null | head -1)
  if [ -z "$LATEST_COMP" ]; then
    echo "오류: comparison 파일이 없습니다 (re:lab-compare 먼저 실행)"
    exit 1
  fi

  echo "=== 실험 결과 → 문서 역류 (backflow) ==="
  echo "  프로젝트: $PROJECT_NAME"
  echo "  소스: $(basename "$LATEST_COMP")"
  echo ""

  # 대상 문서 확인
  SPEC=""
  PROPOSAL=""
  for f in "04-spec/research-spec.md" "04-spec/tech-spec.md"; do
    [ -f "$PROJECT_DIR/$f" ] && SPEC="$f" && break
  done
  for f in "07-proposal/research-proposal.md" "07-proposal/proposal-final.md"; do
    [ -f "$PROJECT_DIR/$f" ] && PROPOSAL="$f" && break
  done

  echo "  갱신 대상:"
  [ -n "$SPEC" ] && echo "    ✓ $SPEC" || echo "    ✗ spec 없음"
  [ -n "$PROPOSAL" ] && echo "    ✓ $PROPOSAL" || echo "    ✗ proposal 없음"
  echo ""

  if [ -z "$SPEC" ] && [ -z "$PROPOSAL" ]; then
    echo "오류: 갱신할 대상 문서가 없습니다."
    exit 1
  fi

  # Gateway 역류 기록 (best-effort)
  CONFIG_FILE="$HOME/.config/asst/config"
  ASST_TOKEN=""
  ASST_GATEWAY="http://100.79.251.53:8000"
  [ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

  if [ -n "$ASST_TOKEN" ]; then
    echo "  Gateway 역류 기록..."
    UPDATES="[]"
    [ -n "$SPEC" ] && UPDATES=$(python3 -c "import json; print(json.dumps([{'target':'$SPEC','section':'성능 수치','metric':'backflow-trigger'}]))" 2>/dev/null || echo "[]")
    curl -sf --max-time 10 -X POST "$ASST_GATEWAY/experiments/backflow" \
      -H "X-Auth-Token: $ASST_TOKEN" -H "Content-Type: application/json" \
      -d "{\"project\":\"$PROJECT_NAME\",\"track\":\"re\",\"source_file\":\"$(basename "$LATEST_COMP")\",\"updates\":$UPDATES}" \
      > /dev/null 2>&1 && echo "    ✓ 기록 완료" || echo "    ✗ Gateway 미접근 (로컬만 진행)"
  fi

  echo ""
  echo "다음 단계:"
  echo "  Claude Code에서 /re:lab-compare 실행 → Step 6 자동 역류"
  echo "  또는 수동: comparison 파일의 수치를 spec/proposal에 반영"
}

# === DEPLOY-DONE (Phase 5 §5.4b — 배포 완료 → Jira 업데이트) ===
cmd_deploy_done() {
  if [ -z "$PROJECT_NAME" ]; then
    echo "오류: 프로젝트명을 입력하세요."
    echo "사용법: bash project.sh deploy-done <이름> [staging|prod]"
    exit 1
  fi

  PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"
  if [ ! -d "$PROJECT_DIR" ]; then
    echo "오류: $PROJECT_NAME 프로젝트가 존재하지 않습니다."
    exit 1
  fi

  ENV="${TRACK:-staging}"  # $3 위치에 staging/prod
  if [ "$ENV" != "staging" ] && [ "$ENV" != "prod" ]; then
    ENV="staging"
  fi

  # dev/ 에서 최근 커밋 정보
  DEV_DIR="$PROJECT_DIR/dev"
  COMMIT_SHA=""
  VERSION=""
  if [ -d "$DEV_DIR/.git" ]; then
    COMMIT_SHA=$(cd "$DEV_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "")
    VERSION=$(cd "$DEV_DIR" && git describe --tags --always 2>/dev/null || echo "$COMMIT_SHA")
  fi

  echo "=== 배포 완료 보고: $PROJECT_NAME ($ENV) ==="
  echo "  버전: ${VERSION:-unknown}"
  echo "  커밋: ${COMMIT_SHA:-unknown}"
  echo ""

  # 배포 기록 — 09-bridge/deploy-log.md 에 append (단순화, 08-final-doc 제거)
  DEPLOY_LOG="$PROJECT_DIR/09-bridge/deploy-log.md"
  TODAY=$(date +%Y-%m-%d)
  if [ ! -f "$DEPLOY_LOG" ]; then
    echo "# 배포 이력 — $PROJECT_NAME" > "$DEPLOY_LOG"
    echo "" >> "$DEPLOY_LOG"
  fi
  cat >> "$DEPLOY_LOG" << DEPLOYEOF

## 배포 기록 ($TODAY $(date +%H:%M))
| 환경 | 버전 | 커밋 | 상태 |
|------|------|------|------|
| $ENV | ${VERSION:-?} | ${COMMIT_SHA:-?} | success |
DEPLOYEOF
  echo "    ✓ $DEPLOY_LOG 갱신"

  # Gateway 배포 기록 (best-effort)
  CONFIG_FILE="$HOME/.config/asst/config"
  ASST_TOKEN=""
  ASST_GATEWAY="http://100.79.251.53:8000"
  [ -f "$CONFIG_FILE" ] && source "$CONFIG_FILE"

  if [ -n "$ASST_TOKEN" ]; then
    echo "  Gateway 배포 기록..."
    curl -sf --max-time 10 -X POST "$ASST_GATEWAY/deploy/report" \
      -H "X-Auth-Token: $ASST_TOKEN" -H "Content-Type: application/json" \
      -d "{\"project\":\"$PROJECT_NAME\",\"environment\":\"$ENV\",\"status\":\"success\",\"version\":\"${VERSION}\",\"commit_sha\":\"${COMMIT_SHA}\"}" \
      > /dev/null 2>&1 && echo "    ✓ 기록 완료" || echo "    ✗ Gateway 미접근"
  fi

  echo ""
  echo "다음 단계:"
  if [ "$ENV" = "staging" ]; then
    echo "  검증 후: bash project.sh deploy-done $PROJECT_NAME prod"
  else
    echo "  마일스톤 리뷰: 09-bridge/jira-breakdown.md 업데이트 · Jira Custom Field 기입"
  fi
}

# === SYNC (맥북 ↔ NAS bare repo ↔ 회사 PC) ===
cmd_sync() {
  if [ -z "$PROJECT_NAME" ]; then
    echo "오류: 프로젝트명을 입력하세요."
    echo "사용법: bash project.sh sync <이름> [push|pull|status]"
    exit 1
  fi

  PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"
  DEV_DIR="$PROJECT_DIR/dev"
  ACTION="${TRACK:-status}"  # $3 위치: push|pull|status

  if [ ! -d "$DEV_DIR" ]; then
    echo "오류: dev/ 디렉토리가 없습니다. 먼저 init-dev를 실행하세요."
    exit 1
  fi

  # NAS bare repo 경로 (ljw-op:/mnt/nas/workspace/repos/{slug}.git)
  NAS_REPO="ljw-op:/mnt/nas/workspace/repos/${PROJECT_NAME}.git"
  COMPANY_HOST="company"  # ssh alias

  # dev/에 git이 초기화되어 있는지 확인
  if [ ! -d "$DEV_DIR/.git" ]; then
    echo "dev/에 git이 초기화되지 않았습니다. 초기화합니다..."
    cd "$DEV_DIR"
    git init
    git add -A
    git commit -m "init: ${PROJECT_NAME} dev/ scaffold"
    cd - > /dev/null
  fi

  # NAS remote가 등록되어 있는지 확인
  cd "$DEV_DIR"
  if ! git remote get-url nas > /dev/null 2>&1; then
    echo "NAS bare repo 설정 중..."

    # ljw-op에 bare repo 생성
    ssh ljw-op "mkdir -p /mnt/nas/workspace/repos && \
      [ -d /mnt/nas/workspace/repos/${PROJECT_NAME}.git ] || \
      git init --bare /mnt/nas/workspace/repos/${PROJECT_NAME}.git" 2>/dev/null

    if [ $? -ne 0 ]; then
      echo "오류: ljw-op에 bare repo 생성 실패. SSH 연결을 확인하세요."
      cd - > /dev/null
      exit 1
    fi

    git remote add nas "$NAS_REPO"
    echo "  ✓ NAS remote 등록: $NAS_REPO"
  fi

  case "$ACTION" in
    push)
      echo "=== sync push: 맥북 → NAS → 회사 PC ==="
      echo ""

      # 1. 맥북 → NAS
      echo "  [1/2] 맥북 → NAS bare repo..."
      git push nas main 2>&1 | sed 's/^/    /'
      if [ $? -ne 0 ]; then
        echo "    ✗ push 실패. 커밋 상태를 확인하세요."
        cd - > /dev/null
        exit 1
      fi
      echo "    ✓ NAS 동기화 완료"

      # 2. 회사 PC에 pull 명령 전송
      echo "  [2/2] 회사 PC pull 트리거..."
      ssh "$COMPANY_HOST" "
        cd ~/projects/${PROJECT_NAME}/dev 2>/dev/null && \
        git pull nas main 2>&1 || \
        echo 'WARN: 회사 PC에 프로젝트 없음 — 최초 clone 필요'
      " 2>&1 | sed 's/^/    /'

      echo ""
      echo "완료. 회사 PC에서 실행 가능:"
      echo "  cd ~/projects/${PROJECT_NAME}/dev && make train"
      ;;

    pull)
      echo "=== sync pull: 회사 PC → NAS → 맥북 ==="
      echo ""

      # 1. 회사 PC → NAS (회사 PC에서 push)
      echo "  [1/2] 회사 PC → NAS bare repo..."
      ssh "$COMPANY_HOST" "
        cd ~/projects/${PROJECT_NAME}/dev 2>/dev/null && \
        git push nas main 2>&1 || \
        echo 'WARN: 회사 PC에서 push 실패'
      " 2>&1 | sed 's/^/    /'

      # 2. 맥북에서 NAS pull
      echo "  [2/2] NAS → 맥북..."
      git pull nas main 2>&1 | sed 's/^/    /'
      echo "    ✓ 맥북 동기화 완료"

      echo ""
      echo "완료. 맥북에서 GSD 실행 가능:"
      echo "  cd $DEV_DIR"
      echo "  /gsd-verify-work 또는 /gsd-ship"
      ;;

    status|st)
      echo "=== sync status: $PROJECT_NAME ==="
      echo ""

      # 로컬 상태
      echo "  [맥북 dev/]"
      echo "    브랜치: $(git branch --show-current)"
      echo "    최근 커밋: $(git log --oneline -1 2>/dev/null || echo '(없음)')"
      echo "    변경사항: $(git status --porcelain | wc -l | tr -d ' ')건"
      echo ""

      # NAS remote 상태
      echo "  [NAS bare repo]"
      if git ls-remote nas HEAD > /dev/null 2>&1; then
        NAS_HEAD=$(git ls-remote nas HEAD 2>/dev/null | awk '{print substr($1,1,7)}')
        LOCAL_HEAD=$(git rev-parse --short HEAD 2>/dev/null)
        echo "    NAS HEAD: $NAS_HEAD"
        echo "    로컬 HEAD: $LOCAL_HEAD"
        if [ "$NAS_HEAD" = "$LOCAL_HEAD" ]; then
          echo "    상태: ✓ 동기화됨"
        else
          AHEAD=$(git rev-list nas/main..HEAD --count 2>/dev/null || echo "?")
          BEHIND=$(git rev-list HEAD..nas/main --count 2>/dev/null || echo "?")
          echo "    상태: ⚠ 차이 있음 (ahead: $AHEAD, behind: $BEHIND)"
        fi
      else
        echo "    상태: ✗ NAS 연결 불가"
      fi
      echo ""

      # 회사 PC 상태
      echo "  [회사 PC]"
      ssh -o ConnectTimeout=5 "$COMPANY_HOST" "
        if [ -d ~/projects/${PROJECT_NAME}/dev/.git ]; then
          cd ~/projects/${PROJECT_NAME}/dev
          echo \"    브랜치: \$(git branch --show-current)\"
          echo \"    최근 커밋: \$(git log --oneline -1)\"
          echo \"    변경사항: \$(git status --porcelain | wc -l)건\"
        else
          echo '    ✗ 프로젝트 없음'
        fi
      " 2>/dev/null || echo "    ✗ 연결 불가"
      ;;

    data)
      # asst project sync h100 data /Users/ljw0904/Datasets/mvtec_ad
      # asst project sync h100 data ~/Datasets/mvtec_ad ~/Datasets/mvtec_ad
      shift 3 2>/dev/null  # skip: project.sh sync <name> data
      LOCAL_DATA="${1:?사용법: asst project sync <이름> data <로컬경로> [원격경로]}"
      REMOTE_DATA="${2:-~/Datasets/$(basename "$LOCAL_DATA")}"

      if [ ! -d "$LOCAL_DATA" ]; then
        echo "오류: 로컬 경로가 존재하지 않습니다: $LOCAL_DATA"
        exit 1
      fi

      # 전송할 크기 계산
      DATA_SIZE=$(du -sh "$LOCAL_DATA" 2>/dev/null | cut -f1)
      FILE_COUNT=$(find "$LOCAL_DATA" -type f | wc -l | tr -d ' ')

      echo "=== sync data: 맥북 → 회사 PC 데이터 전송 ==="
      echo "  로컬: $LOCAL_DATA ($DATA_SIZE, ${FILE_COUNT}개 파일)"
      echo "  원격: $COMPANY_HOST:$REMOTE_DATA"
      echo ""

      # 원격 디렉토리 생성
      ssh "$COMPANY_HOST" "mkdir -p $(dirname "$REMOTE_DATA")" 2>/dev/null

      # rsync with progress
      echo "  전송 중..."
      rsync -avz --progress --stats \
        "$LOCAL_DATA/" \
        "$COMPANY_HOST:$REMOTE_DATA/" \
        2>&1 | while IFS= read -r line; do
          # 진행률 라인만 간결하게 출력
          case "$line" in
            *"to-chk"*|*"to-check"*)
              # rsync 진행률: "filename ... to-chk=123/456"
              remaining=$(echo "$line" | grep -o 'to-chk=[0-9]*/[0-9]*' | head -1)
              if [ -n "$remaining" ]; then
                done_n=$(echo "$remaining" | sed 's/to-chk=\([0-9]*\)\/\([0-9]*\)/\2-\1/' | bc 2>/dev/null)
                total_n=$(echo "$remaining" | sed 's/to-chk=[0-9]*\/\([0-9]*\)/\1/')
                if [ -n "$done_n" ] && [ -n "$total_n" ] && [ "$total_n" -gt 0 ]; then
                  pct=$((done_n * 100 / total_n))
                  printf "\r    [%-50s] %d%% (%d/%d files)" \
                    "$(printf '#%.0s' $(seq 1 $((pct/2))))" \
                    "$pct" "$done_n" "$total_n"
                fi
              fi
              ;;
            "Number of"*|"Total file"*|"sent "*|"total size"*)
              echo "    $line"
              ;;
          esac
        done
      echo ""

      RSYNC_EXIT=${PIPESTATUS[0]}
      if [ "$RSYNC_EXIT" -eq 0 ]; then
        echo ""
        echo "✓ 전송 완료: $COMPANY_HOST:$REMOTE_DATA"
        echo ""
        echo "sas_config.yaml에서 경로를 설정하세요:"
        echo "  mvtec_root: $REMOTE_DATA"
      else
        echo ""
        echo "✗ 전송 실패 (exit=$RSYNC_EXIT)"
      fi
      ;;

    clone)
      echo "=== sync clone: 회사 PC에 최초 clone ==="
      echo ""

      # NAS에 먼저 push
      echo "  [1/2] 맥북 → NAS..."
      git push nas main 2>&1 | sed 's/^/    /'

      # 회사 PC에 clone
      echo "  [2/2] 회사 PC에 clone..."
      ssh "$COMPANY_HOST" "
        mkdir -p ~/projects/${PROJECT_NAME}
        if [ -d ~/projects/${PROJECT_NAME}/dev ]; then
          echo 'dev/ 이미 존재. pull로 전환합니다.'
          cd ~/projects/${PROJECT_NAME}/dev
          git remote get-url nas > /dev/null 2>&1 || git remote add nas $NAS_REPO
          git pull nas main
        else
          git clone $NAS_REPO ~/projects/${PROJECT_NAME}/dev
          cd ~/projects/${PROJECT_NAME}/dev
          git remote rename origin nas
        fi
      " 2>&1 | sed 's/^/    /'

      echo ""
      echo "완료. 회사 PC에서:"
      echo "  cd ~/projects/${PROJECT_NAME}/dev && make setup"
      ;;

    run)
      # asst project sync h100 run make run-baseline
      # asst project sync h100 run "make run-phase2"
      shift 3 2>/dev/null  # skip: project.sh sync <name> run
      REMOTE_CMD="$*"
      if [ -z "$REMOTE_CMD" ]; then
        echo "사용법: bash project.sh sync <이름> run <command>"
        echo ""
        echo "예시:"
        echo "  asst project sync h100 run make run-baseline"
        echo "  asst project sync h100 run make run-phase2"
        echo "  asst project sync h100 run python src/run_experiment.py --exp 3.1 --seed 42"
        exit 1
      fi

      echo "=== sync run: 회사 PC에서 원격 실행 ==="
      echo "  프로젝트: $PROJECT_NAME"
      echo "  명령어: $REMOTE_CMD"
      echo ""

      # 1. 먼저 push (최신 코드 전송)
      echo "  [1/3] 맥북 → NAS → 회사 PC (auto push)..."
      git push nas main 2>&1 | sed 's/^/    /'
      ssh "$COMPANY_HOST" "
        cd ~/projects/${PROJECT_NAME}/dev && \
        git pull nas main 2>/dev/null
      " 2>&1 | sed 's/^/    /'

      # 2. 원격 실행
      echo "  [2/3] 회사 PC에서 실행: $REMOTE_CMD"
      echo "  ─────────────────────────────────────"
      ssh -t "$COMPANY_HOST" "
        cd ~/projects/${PROJECT_NAME}/dev && \
        source .venv/bin/activate 2>/dev/null; \
        $REMOTE_CMD
      "
      RUN_EXIT=$?
      echo "  ─────────────────────────────────────"

      # 3. 결과 pull
      if [ $RUN_EXIT -eq 0 ]; then
        echo "  [3/3] 결과 pull (회사 PC → NAS → 맥북)..."
        ssh "$COMPANY_HOST" "
          cd ~/projects/${PROJECT_NAME}/dev && \
          git add -A && \
          git diff --cached --quiet || git commit -m 'results: $REMOTE_CMD' && \
          git push nas main 2>/dev/null
        " 2>&1 | sed 's/^/    /'
        git pull nas main 2>&1 | sed 's/^/    /'
        echo ""
        echo "✓ 완료. 결과가 로컬에 동기화되었습니다."
      else
        echo ""
        echo "✗ 실행 실패 (exit=$RUN_EXIT). 결과 pull을 건너뜁니다."
      fi
      ;;

    *)
      echo "사용법: bash project.sh sync <이름> [push|pull|status|clone|run]"
      echo ""
      echo "  push    맥북 → NAS → 회사 PC"
      echo "  pull    회사 PC → NAS → 맥북"
      echo "  status  양쪽 동기화 상태 확인"
      echo "  clone   회사 PC에 최초 clone"
      echo "  run     push → 회사 PC에서 실행 → pull (원스톱)"
      echo "  data    맥북 → 회사 PC 데이터셋 전송 (rsync + progress)"
      ;;
  esac

  cd - > /dev/null 2>&1
}

cmd_deploy() {
  if [ -z "$PROJECT_NAME" ]; then
    echo "사용법: bash project.sh deploy <이름> [build|push|pull|run]"
    exit 1
  fi

  PROJECT_DIR="$PROJECTS_DIR/$PROJECT_NAME"
  DEV_DIR="$PROJECT_DIR/dev"
  ACTION="${TRACK:-help}"
  DOCKER_IMAGE="lee35460/sas-lite:${PROJECT_NAME}"

  if [ ! -d "$DEV_DIR" ]; then
    echo "오류: dev/ 디렉토리가 없습니다."
    exit 1
  fi

  case "$ACTION" in
    build)
      echo "=== deploy build: Docker 이미지 빌드 (linux/amd64) ==="
      cd "$DEV_DIR"
      docker build --platform linux/amd64 -t "$DOCKER_IMAGE" .
      echo ""
      echo "✓ 빌드 완료: $DOCKER_IMAGE"
      ;;

    push)
      echo "=== deploy push: 빌드 + Docker Hub 푸시 ==="
      cd "$DEV_DIR"
      docker build --platform linux/amd64 -t "$DOCKER_IMAGE" .
      echo ""
      echo "  Docker Hub에 푸시 중..."
      docker push "$DOCKER_IMAGE"
      echo ""
      echo "✓ 푸시 완료: $DOCKER_IMAGE"
      echo ""
      echo "회사 PC에서:"
      echo "  asst project deploy $PROJECT_NAME pull"
      ;;

    pull)
      echo "=== deploy pull: Docker Hub에서 pull ==="
      ssh "$COMPANY_HOST" "docker pull $DOCKER_IMAGE" 2>&1 | sed 's/^/    /'
      echo ""
      echo "✓ pull 완료. 실행:"
      echo "  asst project deploy $PROJECT_NAME run make run-baseline"
      ;;

    run)
      shift 3 2>/dev/null
      REMOTE_CMD="$*"
      if [ -z "$REMOTE_CMD" ]; then
        echo "사용법: asst project deploy <이름> run <command>"
        echo ""
        echo "예시:"
        echo "  asst project deploy h100 run make run-baseline"
        echo "  asst project deploy h100 run make run-phase2"
        exit 1
      fi

      MVTEC_REMOTE="${MVTEC_ROOT_REMOTE:-~/Datasets/mvtec_ad}"

      echo "=== deploy run: 회사 PC Docker 실행 ==="
      echo "  이미지: $DOCKER_IMAGE"
      echo "  명령어: $REMOTE_CMD"
      echo ""

      ssh -t "$COMPANY_HOST" "
        mkdir -p ~/projects/${PROJECT_NAME}/{results,cache,experiments}
        docker run --rm --gpus all \
          -v $MVTEC_REMOTE:/data/mvtec_ad:ro \
          -v ~/projects/${PROJECT_NAME}/results:/workspace/results \
          -v ~/projects/${PROJECT_NAME}/cache:/workspace/cache \
          -v ~/projects/${PROJECT_NAME}/experiments:/workspace/experiments \
          $DOCKER_IMAGE $REMOTE_CMD
      "
      RUN_EXIT=$?

      if [ $RUN_EXIT -eq 0 ]; then
        echo ""
        echo "  결과 가져오는 중..."
        rsync -avz "$COMPANY_HOST:~/projects/${PROJECT_NAME}/results/" "$DEV_DIR/results/" 2>&1 | tail -3 | sed 's/^/    /'
        rsync -avz "$COMPANY_HOST:~/projects/${PROJECT_NAME}/experiments/" "$DEV_DIR/experiments/" 2>&1 | tail -3 | sed 's/^/    /'
        echo ""
        echo "✓ 완료. 결과: $DEV_DIR/results/"
      else
        echo ""
        echo "✗ 실행 실패 (exit=$RUN_EXIT)"
      fi
      ;;

    *)
      echo "사용법: bash project.sh deploy <이름> [build|push|pull|run]"
      echo ""
      echo "  build   Docker 이미지 빌드 (맥북)"
      echo "  push    빌드 + Docker Hub 푸시 (맥북)"
      echo "  pull    Docker Hub에서 pull (회사 PC)"
      echo "  run     회사 PC에서 Docker 실행 + 결과 pull"
      echo ""
      echo "흐름:"
      echo "  asst project deploy h100 push             # 1회: 빌드+푸시"
      echo "  asst project deploy h100 pull             # 회사 PC: pull"
      echo "  asst project deploy h100 run make run-baseline  # 실험"
      ;;
  esac

  cd - > /dev/null 2>&1
}

COMPANY_HOST="${COMPANY_HOST:-company}"

# === MAIN ===
case "$CMD" in
  new)         cmd_new ;;
  init-dev)    cmd_init_dev ;;
  sync)        cmd_sync "$@" ;;
  deploy)      cmd_deploy "$@" ;;
  backflow)    cmd_backflow ;;
  deploy-done) cmd_deploy_done ;;
  delete)      cmd_delete ;;
  list)        cmd_list ;;
  status)      cmd_status ;;
  *)           show_help ;;
esac
