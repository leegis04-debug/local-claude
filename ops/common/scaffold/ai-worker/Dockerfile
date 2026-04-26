ARG BASE_IMAGE=local-claude/ops-ml:latest
FROM ${BASE_IMAGE}

WORKDIR /workspace

# 기본 의존성 (CPU 호환, 모든 환경에서 동작)
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# CUDA 환경 자동 감지 → onnxruntime-gpu 추가 설치
# (CUDA base 이미지에서만 활성화. CPU base에서는 스킵 → CPU 폴백)
RUN if [ -d /usr/local/cuda ] || command -v nvidia-smi >/dev/null 2>&1; then \
      echo "CUDA detected — installing onnxruntime-gpu"; \
      pip3 install --no-cache-dir --break-system-packages onnxruntime-gpu || true; \
    else \
      echo "CPU-only environment — onnxruntime CPU 사용"; \
    fi

# 빌드 시점에 모델 사전 다운로드를 원하면 여기서 (옵션):
# RUN python3 -c "from transformers import AutoModel; AutoModel.from_pretrained('bert-base-uncased')"

# 코드 포함 (이미지 자체로 실행 가능)
COPY . .

# 헬스체크 (어떤 host 에서든 컨테이너 헬스 표시 — Docker swarm/K8s/compose 호환)
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=20s \
  CMD curl -fsS http://localhost:8001/health || exit 1

# 런타임 메타데이터 (배포 시 이미지 검증용)
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

EXPOSE 8001
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8001"]
