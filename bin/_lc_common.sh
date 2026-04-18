#!/usr/bin/env bash
# local-claude shim 공유 루틴. jw/re 에서 source 한다.
# 원본 wrapper 는 수정하지 않는다 — 이 shim 을 PATH 앞쪽에 두기만 하면 훅이 붙는다.

_lc_root() {
  cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd
}

# 실행할 Python 을 결정한다.
# 우선순위: (1) $LC_PYTHON (2) repo-local .venv (3) $PATH 의 python3
_lc_pick_python() {
  local lc_root="$1"
  if [ -n "${LC_PYTHON:-}" ] && [ -x "${LC_PYTHON}" ]; then
    echo "$LC_PYTHON"
    return
  fi
  if [ -x "$lc_root/.venv/bin/python3" ]; then
    echo "$lc_root/.venv/bin/python3"
    return
  fi
  echo "${LC_PYTHON:-python3}"
}

# PATH 에서 shim 디렉토리를 제외하고 원본 실행파일을 찾는다.
_lc_find_original() {
  local cmd="$1"
  local lc_bin="$2"
  local orig_path="${PATH}"
  # 선행/중간/후행 케이스 모두 제거
  orig_path="${orig_path//"$lc_bin":/}"
  orig_path="${orig_path//:"$lc_bin"/}"
  orig_path="${orig_path//"$lc_bin"/}"
  local resolved
  resolved="$(PATH="$orig_path" command -v "$cmd" 2>/dev/null || true)"
  if [ -z "$resolved" ] || [ "$resolved" = "$lc_bin/$cmd" ]; then
    if [ -x "$HOME/.local/bin/$cmd" ]; then
      resolved="$HOME/.local/bin/$cmd"
    fi
  fi
  echo "$resolved"
}

# perf/state 훅을 감싸 원본 wrapper 를 실행한다.
# 실패는 전부 조용히 무시 — 원본 명령이 lc 훅 때문에 멎으면 안 된다.
_lc_invoke() {
  local cmd="$1"
  shift

  local lc_root
  lc_root="$(cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd)"
  local lc_bin="$lc_root/bin"
  local py
  py="$(_lc_pick_python "$lc_root")"
  export PYTHONPATH="$lc_root/src${PYTHONPATH:+:$PYTHONPATH}"

  local original
  original="$(_lc_find_original "$cmd" "$lc_bin")"
  if [ -z "$original" ]; then
    echo "local-claude/bin/$cmd: 원본 $cmd 를 찾을 수 없음 (\$PATH 또는 ~/.local/bin)" >&2
    return 127
  fi

  local action="$cmd"
  if [ -n "${1:-}" ]; then
    action="$cmd $1"
  fi
  local project
  project="$(pwd)"

  # 실행 전 state 훅 — last_action 선점 기록.
  "$py" -m local_claude.cli -p "$project" state update --last-action "$action" >/dev/null 2>&1 || true

  # 고해상도 타이밍 — macOS date 는 %N 미지원이라 python fallback.
  local start_ms
  start_ms="$("$py" -c 'import time; print(int(time.time()*1000))' 2>/dev/null || echo 0)"

  "$original" "$@"
  local exit_code=$?

  local end_ms
  end_ms="$("$py" -c 'import time; print(int(time.time()*1000))' 2>/dev/null || echo 0)"
  local duration_ms=0
  if [ "$start_ms" != "0" ] && [ "$end_ms" != "0" ]; then
    duration_ms=$((end_ms - start_ms))
  fi

  "$py" -m local_claude.cli -p "$project" perf log \
    --action "$action" \
    --duration-ms "$duration_ms" \
    --exit-code "$exit_code" >/dev/null 2>&1 || true
  "$py" -m local_claude.cli -p "$project" state update \
    --last-action "$action" \
    --exit-code "$exit_code" >/dev/null 2>&1 || true

  # 자동 기억 (Plan §② — 매 턴 종료 후 LLM 에게 기억할 것 추출).
  # 기본 off — LC_AUTO_MEMORY=1 로 opt-in. Gemma 호출이라 백그라운드 분리.
  if [ "${LC_AUTO_MEMORY:-0}" = "1" ]; then
    local snippet
    snippet="action=$action exit=$exit_code cwd=$project duration_ms=$duration_ms"
    (
      echo "$snippet" | \
        "$py" -m local_claude.cli -p "$project" memory auto --stdin \
        >/dev/null 2>&1 || true
    ) &
    disown 2>/dev/null || true
  fi

  return "$exit_code"
}
