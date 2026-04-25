#!/bin/bash
# Squad 병렬 자동 실행
# tmux 세션에 여러 에이전트를 자동으로 띄움
#
# 사용법:
#   bash squad-run.sh <프로젝트명> <패턴> [주제]
#
# 예시:
#   bash squad-run.sh meet-and-future idea "AI 기반 육류 숙성 스마트관리"
#   bash squad-run.sh pilot proposal "스마트팩토리 솔루션"
#   bash squad-run.sh meet-and-future spec

set -e

PROJECT_NAME="$1"
PATTERN="$2"
TOPIC="${3:-프로젝트 주제}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
PROJECT_DIR="$BASE_DIR/projects/$PROJECT_NAME"

if [ -z "$PROJECT_NAME" ] || [ -z "$PATTERN" ]; then
  echo "사용법: bash squad-run.sh <프로젝트명> <패턴> [주제]"
  echo ""
  echo "패턴:"
  echo "  idea     — analyst + strategist + architect (3개)"
  echo "  proposal — writer + architect + analyst + writer + critic (5개)"
  echo "  spec     — architect + analyst + critic + writer (4개)"
  echo "  attack   — writer → critic + analyst → integrator (3라운드)"
  echo ""
  echo "예시:"
  echo "  bash squad-run.sh meet-and-future idea \"AI 기반 육류 숙성\""
  exit 1
fi

if [ ! -d "$PROJECT_DIR/.git" ]; then
  echo "오류: $PROJECT_DIR 이 git repo가 아닙니다."
  echo "먼저: bash claude/ops/new-project.sh $PROJECT_NAME"
  exit 1
fi

SESSION="squad-$PROJECT_NAME"

# 기존 tmux 세션 정리
tmux kill-session -t "$SESSION" 2>/dev/null || true

case "$PATTERN" in
  idea)
    AGENTS=("jw-analyst" "jw-strategist" "jw-architect")
    PROMPTS=(
      "01-idea/problem-definition.md에 작성하라. 주제: $TOPIC. 이 문제의 존재, 규모, 원인을 분석하라."
      "01-idea/market-demand.md에 작성하라. 주제: $TOPIC. 시장 수요, 경쟁 환경, 타이밍을 분석하라."
      "01-idea/tech-feasibility.md에 작성하라. 주제: $TOPIC. 기술 가능성, 아키텍처, 구현 난이도를 분석하라."
    )
    ;;
  proposal)
    AGENTS=("jw-writer" "jw-architect" "jw-analyst" "jw-writer" "jw-critic")
    PROMPTS=(
      "05-proposal/background.md에 작성하라. 추진배경과 문제 심각성을 수치와 함께 서술하라."
      "05-proposal/tech-content.md에 작성하라. 기술개발 내용과 시스템 구성을 명세 기반으로 작성하라."
      "05-proposal/kpi-targets.md에 작성하라. 정량 목표와 KPI를 설계하고 달성 근거를 제시하라."
      "05-proposal/expected-effects.md에 작성하라. 기대효과와 사업화 전략을 작성하라."
      "05-proposal/review-defense.md에 작성하라. 심사평가 기준으로 전체 계획의 약점을 찾아라."
    )
    ;;
  spec)
    AGENTS=("jw-architect" "jw-analyst" "jw-critic" "jw-writer")
    PROMPTS=(
      "04-spec/architecture.md에 작성하라. 시스템 아키텍처와 모듈 구성을 설계하라."
      "04-spec/data-flow.md에 작성하라. 데이터 흐름과 입출력을 명세하라."
      "04-spec/scope-and-risk.md에 작성하라. 구현 범위와 리스크를 정리하라."
      "04-spec/validation-design.md에 작성하라. 실증 시나리오와 평가 항목을 설계하라."
    )
    ;;
  *)
    echo "알 수 없는 패턴: $PATTERN"
    exit 1
    ;;
esac

COUNT=${#AGENTS[@]}
echo "=== Squad 병렬 실행: $PATTERN ($COUNT개 에이전트) ==="
echo "프로젝트: $PROJECT_DIR"
echo "주제: $TOPIC"
echo ""

# 첫 번째 에이전트로 tmux 세션 생성
echo "[1/$COUNT] ${AGENTS[0]} 시작..."
tmux new-session -d -s "$SESSION" -c "$PROJECT_DIR" \
  "claude-squad -y -p 'claude --agent=${AGENTS[0]}'"

# 나머지 에이전트를 새 윈도우로 추가
for i in $(seq 1 $((COUNT - 1))); do
  idx=$((i + 1))
  echo "[$idx/$COUNT] ${AGENTS[$i]} 시작..."
  tmux new-window -t "$SESSION" -c "$PROJECT_DIR" \
    "claude-squad -y -p 'claude --agent=${AGENTS[$i]}'"
done

echo ""
echo "=== 모든 에이전트 실행됨 ==="
echo ""
echo "접속: tmux attach -t $SESSION"
echo "윈도우 전환: Ctrl+B → 숫자(0,1,2...)"
echo "빠져나오기: Ctrl+B → D (에이전트는 계속 작업)"
echo "종료: tmux kill-session -t $SESSION"
echo ""
echo "각 에이전트에 접속 후 n → 세션 생성 → 프롬프트 입력:"
for i in $(seq 0 $((COUNT - 1))); do
  echo "  [윈도우 $i] ${AGENTS[$i]}: \"${PROMPTS[$i]}\""
done
echo ""
echo "모든 에이전트 완료 후:"
echo "  1. 각 윈도우에서 c → s → q"
echo "  2. git branch 로 worktree 브랜치 확인"
echo "  3. git merge 각 브랜치"
echo "  4. claude-squad -p 'claude --agent=jw-integrator' 로 통합"

# tmux 세션에 접속
tmux attach -t "$SESSION"
