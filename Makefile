# local-claude root Makefile — 공통 base 이미지 빌드
#
# 빌드 환경 자동 감지:
#   - amd64 native (Linux 서버 등) → docker build (기본)
#   - 다른 아키텍처 (M2 등) → docker buildx --platform linux/amd64 (emulation)
#
# 처음 한 번만 실행:
#   make build-base    # ubuntu:24.04 + python3.12 + gcc/cmake
#   make build-ml      # nvidia/cuda:12.6.3 + opencv 런타임 (~6GB, GPU 서버 권장)

PLATFORM ?= linux/amd64
HOST_ARCH := $(shell uname -m)

# amd64 호스트면 native build, 아니면 buildx (emulation)
ifeq ($(HOST_ARCH),x86_64)
  BUILD_CMD := docker build
else
  BUILD_CMD := docker buildx build --platform $(PLATFORM) --load
endif

.PHONY: build-base build-ml build-all help

build-base:  ## base 이미지 빌드 (python3.12 + gcc)
	$(BUILD_CMD) -t local-claude/ops-base:latest ops/docker/base/
	@echo "✓ local-claude/ops-base:latest (host=$(HOST_ARCH))"

build-ml:  ## ML base 이미지 빌드 (CUDA 12.6.3)
	$(BUILD_CMD) -t local-claude/ops-ml:latest ops/docker/ml/
	@echo "✓ local-claude/ops-ml:latest (host=$(HOST_ARCH))"

build-all: build-base build-ml  ## 두 base 이미지 모두 빌드

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
