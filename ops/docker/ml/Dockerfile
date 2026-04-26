FROM nvidia/cuda:12.6.3-cudnn-devel-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv python3-pip \
    gcc g++ cmake make git curl ca-certificates \
    libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --break-system-packages \
    ruff mypy pytest pytest-cov

WORKDIR /workspace
