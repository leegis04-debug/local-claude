FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip \
    gcc g++ cmake make git curl wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --break-system-packages \
    ruff mypy pytest pytest-cov pre-commit

WORKDIR /workspace
