#!/bin/bash
# deploy.sh — 비서 OS DevOps 배포 스크립트
# 스테이징 테스트 → 스냅샷 → 프로덕션 배포 → 검증 → (실패 시 롤백)
#
# 사용법:
#   bash deploy.sh staging    — 스테이징 환경에서 테스트
#   bash deploy.sh snapshot   — 현재 프로덕션 스냅샷 생성
#   bash deploy.sh deploy     — 프로덕션 배포 (snapshot 자동 생성)
#   bash deploy.sh rollback   — 마지막 스냅샷으로 롤백
#   bash deploy.sh verify     — 프로덕션 health check
#   bash deploy.sh status     — 현재 상태 확인

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
REMOTE="ljw-op"
GPU_REMOTE="100.105.221.243"
COMPOSE_DIR="/opt/assistant/compose"
SNAPSHOT_DIR="/opt/assistant/snapshots"
GATEWAY_URL="http://100.79.251.53:8000"

# Load token
ASST_TOKEN="${ASST_TOKEN:-}"
[ -f "$HOME/.config/asst/config" ] && source "$HOME/.config/asst/config"

CMD="${1:-status}"

log() { echo "[$(date +%H:%M:%S)] $1"; }

# === SNAPSHOT ===
cmd_snapshot() {
  log "=== 프로덕션 스냅샷 생성 ==="
  SNAP_ID="$(date +%Y%m%d_%H%M%S)"

  ssh $REMOTE "sudo mkdir -p $SNAPSHOT_DIR/$SNAP_ID"

  # 1. Docker images
  log "  Docker 이미지 태그 저장..."
  ssh $REMOTE "docker ps --format '{{.Image}}' | sort -u > $SNAPSHOT_DIR/$SNAP_ID/images.txt"

  # 2. Postgres dump
  log "  Postgres 전체 백업..."
  ssh $REMOTE "docker exec assistant-postgres pg_dumpall -U postgres > $SNAPSHOT_DIR/$SNAP_ID/pg_dump.sql"

  # 3. Compose file
  log "  Compose 설정 백업..."
  ssh $REMOTE "cp $COMPOSE_DIR/docker-compose.yml $SNAPSHOT_DIR/$SNAP_ID/"

  # 4. Git hash
  log "  Git 커밋 해시 기록..."
  git rev-parse HEAD > /tmp/deploy_git_hash
  scp /tmp/deploy_git_hash $REMOTE:$SNAPSHOT_DIR/$SNAP_ID/git_hash.txt 2>/dev/null

  # 5. ai-worker snapshot
  log "  ai-worker 이미지 태그..."
  ssh $GPU_REMOTE "docker images ai-worker --format '{{.ID}}' | head -1" > /tmp/aiworker_id
  scp /tmp/aiworker_id $REMOTE:$SNAPSHOT_DIR/$SNAP_ID/aiworker_id.txt 2>/dev/null

  log "  ✓ 스냅샷 생성 완료: $SNAP_ID"
  echo "$SNAP_ID"
}

# === STAGING ===
cmd_staging() {
  log "=== 스테이징 환경 테스트 ==="

  # 1. 코드 동기화 (staging 브랜치에서)
  log "  코드 동기화..."
  rsync -az "$REPO_DIR/infra/gateway/" $REMOTE:/opt/assistant/gateway/ 2>/dev/null
  rsync -az "$REPO_DIR/infra/ai-worker/" $GPU_REMOTE:~/ai-worker/ 2>/dev/null

  # 2. gateway 빌드 테스트 (실제 재시작 안 함)
  log "  Gateway 빌드 테스트..."
  ssh $REMOTE "cd $COMPOSE_DIR && docker compose --profile day34 --profile openclaw build gateway" 2>&1 | tail -3

  # 3. ai-worker 빌드 테스트
  log "  ai-worker 빌드 테스트..."
  ssh $GPU_REMOTE "cd ~/ai-worker && docker build -t ai-worker:staging ." 2>&1 | tail -3

  # 4. 문법 검증 (compose config)
  log "  Compose 설정 검증..."
  ssh $REMOTE "cd $COMPOSE_DIR && docker compose --profile day34 --profile openclaw config > /dev/null" 2>&1

  log "  ✓ 스테이징 빌드 성공"
  log ""
  log "  다음: bash deploy.sh deploy (프로덕션 배포)"
}

# === DEPLOY ===
cmd_deploy() {
  log "=== 프로덕션 배포 ==="

  # 0. 자동 스냅샷
  log "  배포 전 스냅샷 생성..."
  SNAP_ID=$(cmd_snapshot)

  # 1. Gateway 배포
  log "  Gateway 재시작..."
  rsync -az "$REPO_DIR/infra/gateway/" $REMOTE:/opt/assistant/gateway/
  ssh $REMOTE "cd $COMPOSE_DIR && docker compose --profile day34 --profile openclaw build gateway && docker compose --profile day34 --profile openclaw up -d --force-recreate gateway" 2>&1 | tail -3

  # 2. ai-worker 배포
  log "  ai-worker 재시작..."
  rsync -az "$REPO_DIR/infra/ai-worker/" $GPU_REMOTE:~/ai-worker/
  ssh $GPU_REMOTE "cd ~/ai-worker && docker build -t ai-worker . && docker stop ai-worker && docker rm ai-worker && docker run -d --name ai-worker --network host --restart unless-stopped -v \$HOME/ai-worker/prompts:/app/prompts:ro ai-worker" 2>&1 | tail -3

  # 3. n8n 워크플로 동기화
  log "  n8n 워크플로 동기화..."
  rsync -az "$REPO_DIR/infra/n8n/workflows/" $REMOTE:$COMPOSE_DIR/n8n/workflows/

  # 4. Compose 동기화
  log "  Compose 설정 동기화..."
  rsync -az "$REPO_DIR/infra/compose/docker-compose.yml" $REMOTE:$COMPOSE_DIR/docker-compose.yml

  # 5. 검증
  log "  배포 후 검증..."
  sleep 3
  cmd_verify

  # 6. P-Reinforce 메모
  if [ -n "$ASST_TOKEN" ]; then
    curl -sf -X POST "$GATEWAY_URL/notes" \
      -H "X-Auth-Token: $ASST_TOKEN" -H "Content-Type: application/json" \
      -d "{\"text\":\"[deploy] 프로덕션 배포 완료. snapshot=$SNAP_ID, git=$(git rev-parse --short HEAD)\",\"source\":\"cli\",\"tags\":[\"deploy\",\"devops\"]}" >/dev/null 2>&1
  fi

  log "  ✓ 배포 완료 (rollback: bash deploy.sh rollback)"
}

# === ROLLBACK ===
cmd_rollback() {
  log "=== 롤백 ==="

  # 최신 스냅샷 찾기
  SNAP_ID=$(ssh $REMOTE "ls -t $SNAPSHOT_DIR/ | head -1" 2>/dev/null)
  if [ -z "$SNAP_ID" ]; then
    log "  ✗ 스냅샷 없음. 롤백 불가."
    exit 1
  fi

  log "  스냅샷: $SNAP_ID"

  # 1. Git checkout
  GIT_HASH=$(ssh $REMOTE "cat $SNAPSHOT_DIR/$SNAP_ID/git_hash.txt" 2>/dev/null)
  if [ -n "$GIT_HASH" ]; then
    log "  Git 복원: $GIT_HASH"
    git checkout $GIT_HASH -- infra/
  fi

  # 2. Compose 복원
  log "  Compose 복원..."
  ssh $REMOTE "cp $SNAPSHOT_DIR/$SNAP_ID/docker-compose.yml $COMPOSE_DIR/"

  # 3. 재배포
  log "  서비스 재시작..."
  rsync -az "$REPO_DIR/infra/gateway/" $REMOTE:/opt/assistant/gateway/
  ssh $REMOTE "cd $COMPOSE_DIR && docker compose --profile day34 --profile openclaw build gateway && docker compose --profile day34 --profile openclaw up -d --force-recreate gateway" 2>&1 | tail -3

  # 4. Postgres 복원 (선택 — 데이터 손실 위험)
  log "  ⚠ DB 복원은 수동: docker exec -i assistant-postgres psql -U postgres < $SNAPSHOT_DIR/$SNAP_ID/pg_dump.sql"

  log "  ✓ 롤백 완료"
}

# === VERIFY ===
cmd_verify() {
  log "  검증 중..."

  # Gateway health
  GW=$(curl -sf "$GATEWAY_URL/health" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','fail'))" 2>/dev/null)
  [ "$GW" = "ok" ] && log "    ✓ Gateway: OK" || log "    ✗ Gateway: FAIL"

  # Gateway ready (upstreams)
  READY=$(curl -sf "$GATEWAY_URL/ready" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('ready',False))" 2>/dev/null)
  [ "$READY" = "True" ] && log "    ✓ Upstreams: OK" || log "    ✗ Upstreams: FAIL"

  # ai-worker
  AI=$(ssh $GPU_REMOTE "curl -sf http://localhost:8080/health" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','fail'))" 2>/dev/null)
  [ "$AI" = "ok" ] && log "    ✓ ai-worker: OK" || log "    ✗ ai-worker: FAIL"

  # Container count
  CONTAINERS=$(ssh $REMOTE "docker ps --format '{{.Names}}' | grep assistant | wc -l" 2>/dev/null)
  log "    ✓ Containers: $CONTAINERS running"

  if [ "$GW" != "ok" ] || [ "$READY" != "True" ] || [ "$AI" != "ok" ]; then
    log "  ⚠ 검증 실패 — rollback 권장: bash deploy.sh rollback"
    return 1
  fi
  log "  ✓ 전체 검증 통과"
}

# === STATUS ===
cmd_status() {
  log "=== 시스템 상태 ==="
  log "  Git: $(git rev-parse --short HEAD) ($(git log --oneline -1 | cut -c9-))"
  log "  Branch: $(git branch --show-current)"
  cmd_verify
  log ""
  SNAPS=$(ssh $REMOTE "ls $SNAPSHOT_DIR/ 2>/dev/null | wc -l" 2>/dev/null)
  log "  스냅샷: ${SNAPS:-0}개"
  [ "${SNAPS:-0}" -gt 0 ] && log "  최신: $(ssh $REMOTE "ls -t $SNAPSHOT_DIR/ | head -1" 2>/dev/null)"
}

case "$CMD" in
  staging)   cmd_staging ;;
  snapshot)  cmd_snapshot ;;
  deploy)    cmd_deploy ;;
  rollback)  cmd_rollback ;;
  verify)    cmd_verify ;;
  status)    cmd_status ;;
  *)
    echo "비서 OS DevOps"
    echo ""
    echo "사용법:"
    echo "  bash deploy.sh staging    스테이징 빌드 테스트"
    echo "  bash deploy.sh snapshot   현재 상태 스냅샷"
    echo "  bash deploy.sh deploy     프로덕션 배포 (자동 스냅샷 + 검증)"
    echo "  bash deploy.sh rollback   마지막 스냅샷으로 롤백"
    echo "  bash deploy.sh verify     health check"
    echo "  bash deploy.sh status     전체 상태"
    ;;
esac
