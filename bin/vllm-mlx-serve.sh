#!/usr/bin/env bash
# vllm-mlx-serve — 맥북 vllm-mlx (MLX 백엔드) 데몬 기동 래퍼.
#
# 기본: Qwen 2.5 3B Instruct (4-bit) + bge-m3 (fp16) 임베딩을 한 프로세스에서 서빙.
# launchd plist(com.local-claude.vllm-mlx.plist) 가 이 스크립트를 호출한다.
#
# 모델 선정 근거:
#   selector 가 시스템 throughput 의 70-95% 차지 (gjw idea 1회당 200-1000 call,
#   yes/no 짧은 응답). Qwen 14B 4-bit 는 6s/call → selector 만 20-100분.
#   Qwen 2.5 3B 4-bit (text-only) 로 selector latency 0.5s/call 기대 → 1-10분.
#   Gemma 3·4 모두 multimodal(VLM) 분류로 vllm-mlx 0.2.9 의 mlx_vlm + asyncio
#   GPU stream 버그 (waybarrios/vllm-mlx#380, 2026-04-20) 발생 — 사용 불가.
#   Qwen 2.5 (text-only) 는 SimpleEngine 경로로 정상 동작 검증됨.
#
# env override:
#   VLLM_MLX_PORT         포트 (default 11434)
#   VLLM_MLX_HOST         바인드 host (default 127.0.0.1)
#   VLLM_MLX_MODEL        LLM 모델 repo (default mlx-community/Qwen2.5-3B-Instruct-4bit)
#   VLLM_MLX_MODEL_NAME   /v1/models 에서 보일 이름 (default qwen-2.5-3b)
#   VLLM_MLX_EMBEDDING    embedding 모델 repo. 빈 문자열이면 LLM 단독.
#                         (default mlx-community/bge-m3-mlx-fp16)
#   VLLM_MLX_KV_QUANT     1 = KV cache 8-bit 양자화 (default 1)
#   VLLM_MLX_CONTINUOUS   1 = --continuous-batching 활성 (default 0).
#                         0.2.9 에서 mlx_lm prompt_cache thread 격리 버그로
#                         두 번째 요청 부터 'There is no Stream(gpu,X)' 발생 →
#                         단일 사용자(맥북 selector·enrich)에는 OFF 권장.
#   VLLM_MLX_EXTRA_ARGS   추가 인자 문자열 (예: "--max-num-seqs 8")

set -eu

REPO_ROOT="${VLLM_MLX_REPO_ROOT:-$HOME/workspace/local-claude}"
VENV_PY="$REPO_ROOT/.venv/bin/python"
VENV_BIN="$REPO_ROOT/.venv/bin/vllm-mlx"

if [ ! -x "$VENV_BIN" ]; then
  echo "[vllm-mlx-serve] error: $VENV_BIN not found — run 'pip install vllm-mlx' in .venv" >&2
  exit 1
fi

PORT="${VLLM_MLX_PORT:-11434}"
HOST="${VLLM_MLX_HOST:-127.0.0.1}"
MODEL="${VLLM_MLX_MODEL:-mlx-community/Qwen2.5-3B-Instruct-4bit}"
MODEL_NAME="${VLLM_MLX_MODEL_NAME:-qwen-2.5-3b}"
EMBEDDING="${VLLM_MLX_EMBEDDING-mlx-community/bge-m3-mlx-fp16}"
KV_QUANT="${VLLM_MLX_KV_QUANT:-1}"
CONTINUOUS="${VLLM_MLX_CONTINUOUS:-0}"
EXTRA_ARGS="${VLLM_MLX_EXTRA_ARGS:-}"

ARGS=(
  serve "$MODEL"
  --host "$HOST"
  --port "$PORT"
  --served-model-name "$MODEL_NAME"
)

if [ "$CONTINUOUS" = "1" ]; then
  ARGS+=(--continuous-batching)
fi

if [ -n "$EMBEDDING" ]; then
  ARGS+=(--embedding-model "$EMBEDDING")
fi

if [ "$KV_QUANT" = "1" ]; then
  ARGS+=(--kv-cache-quantization --kv-cache-quantization-bits 8)
fi

if [ -n "$EXTRA_ARGS" ]; then
  # shellcheck disable=SC2206
  EXTRA=($EXTRA_ARGS)
  ARGS+=("${EXTRA[@]}")
fi

echo "[vllm-mlx-serve] $(date -u +%Y-%m-%dT%H:%M:%SZ) launching: $VENV_BIN ${ARGS[*]}"
exec "$VENV_BIN" "${ARGS[@]}"
