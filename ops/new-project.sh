#!/bin/bash
# 다겸 새 프로젝트 생성 스크립트 (레거시 — project.sh new 사용 권장)
# 사용법: bash new-project.sh <프로젝트명> [jw|re]

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "※ new-project.sh는 project.sh new로 통합되었습니다."
echo "   실행: bash project.sh new $1 $2"
echo ""

exec bash "$SCRIPT_DIR/project.sh" new "$@"
