#!/usr/bin/env bash
# local-claude 설치 — shell rc 에 PATH 한 줄만 추가한다.
# 외부 디렉토리를 건드리지 않는다. 사용자 데이터(~/.local-claude/) 는 첫 사용 시 코드가 자동 생성.
set -euo pipefail

LC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$LC_ROOT/bin"
MARKER="# local-claude"
LINE="export PATH=\"$BIN_DIR:\$PATH\"  $MARKER"

chmod +x "$BIN_DIR"/jw "$BIN_DIR"/re "$BIN_DIR"/lcai "$BIN_DIR"/_lc_common.sh

# 전용 venv 구성 — conda/homebrew 혼재 환경에서 PEP 668 충돌 회피.
VENV_DIR="$LC_ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python3"
HOST_PY="${LC_PYTHON:-python3}"

if ! command -v "$HOST_PY" >/dev/null 2>&1; then
  echo "  WARN: $HOST_PY 를 찾을 수 없음 — venv 생성 불가"
else
  if [ ! -x "$VENV_PY" ]; then
    echo "[venv] 생성: $VENV_DIR"
    "$HOST_PY" -m venv "$VENV_DIR"
  fi
  if [ -x "$VENV_PY" ]; then
    echo "[deps] $VENV_PY -m pip install httpx pydantic"
    "$VENV_PY" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
    if ! "$VENV_PY" -m pip install --quiet "httpx>=0.27" "pydantic>=2.0"; then
      echo "  WARN: venv pip 설치 실패"
    fi
  fi
fi

added_to=()
skipped=()
for RC in "$HOME/.zshrc" "$HOME/.bashrc"; do
  if [ ! -f "$RC" ]; then
    continue
  fi
  if grep -Fq "$MARKER" "$RC" 2>/dev/null; then
    skipped+=("$RC")
    continue
  fi
  printf '\n%s\n' "$LINE" >> "$RC"
  added_to+=("$RC")
done

echo "local-claude 설치 완료"
echo "  repo      : $LC_ROOT"
echo "  shim PATH : $BIN_DIR"
echo "  CLI 이름  : lcai (+ jw/re shim)"
if [ "${#added_to[@]}" -gt 0 ]; then
  printf '  추가 대상 : %s\n' "${added_to[@]}"
fi
if [ "${#skipped[@]}" -gt 0 ]; then
  printf '  이미 등록: %s\n' "${skipped[@]}"
fi
echo
echo "새 터미널을 열거나:  source ~/.zshrc"
echo "제거:                $LC_ROOT/uninstall.sh"
