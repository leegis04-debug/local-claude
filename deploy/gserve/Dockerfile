# G serve Docker 이미지 — 미니 PC(x86/Docker) 상주용.
# build:  docker build -t gstar-serve:latest -f deploy/gserve/Dockerfile .
# run:    docker run -d -p 9999:9999 -v /volume1/docker/gstar-data:/app/state \
#             --name g-serve gstar-serve:latest

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GSTAR_HOME=/app/state \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY README.md ./

RUN pip install --upgrade pip && \
    pip install '.[gstar,entity,input]' fastapi uvicorn[standard]

# G 서버가 쓸 상태 디렉토리 (bind mount 권장)
RUN mkdir -p /app/state

EXPOSE 9999

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:9999/health || exit 1

# --workers 1 강제: DuckDB single-writer 전제. 다중 worker 시 락 충돌·corruption 위험.
CMD ["uvicorn", "gstar.serve:app", "--host", "0.0.0.0", "--port", "9999", "--workers", "1"]
