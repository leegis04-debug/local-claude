#!/usr/bin/env bash
# local-claude 제거 — shell rc 에서 PATH 라인만 삭제한다.
# 사용자 데이터(~/.local-claude/) 와 프로젝트 로컬 state.json/.memory/.perf 는 건드리지 않는다.
set -euo pipefail

MARKER="# local-claude"

removed=()
skipped=()
for RC in "$HOME/.zshrc" "$HOME/.bashrc"; do
  if [ ! -f "$RC" ]; then
    continue
  fi
  if ! grep -Fq "$MARKER" "$RC" 2>/dev/null; then
    skipped+=("$RC")
    continue
  fi
  tmp="$(mktemp)"
  grep -Fv "$MARKER" "$RC" > "$tmp" || true
  mv "$tmp" "$RC"
  removed+=("$RC")
done

echo "local-claude 제거 완료"
if [ "${#removed[@]}" -gt 0 ]; then
  printf '  정리 대상: %s\n' "${removed[@]}"
fi
if [ "${#skipped[@]}" -gt 0 ]; then
  printf '  미등록   : %s\n' "${skipped[@]}"
fi
echo
echo "새 터미널을 열거나:  source ~/.zshrc"
echo "데이터 디렉토리(~/.local-claude/) 와 프로젝트 로컬 상태는 수동으로 지우세요."
