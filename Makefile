# local-claude root Makefile — 공통 base 이미지 빌드 (amd64 고정)
#
# 모든 base 이미지는 linux/amd64 로 빌드.
# 이유: GPU 서버(amd64+CUDA)와 동일한 아키텍처여야 같은 이미지로 배포 가능.
# Apple Silicon 맥북에서는 Rosetta 2 emulation 으로 실행됨 (검증용).
#
# 처음 한 번만 실행:
#   make build-base    # ubuntu:24.04 + python3.12 + gcc/cmake
#   make build-ml      # nvidia/cuda:12.6.3 + opencv 런타임 (~6GB)

PLATFORM ?= linux/amd64

.PHONY: build-base build-ml build-all help

build-base:  ## base 이미지 빌드 (amd64, python3.12 + gcc)
	docker buildx build --platform $(PLATFORM) --load \
	  -t local-claude/ops-base:latest ops/docker/base/
	@echo "✓ local-claude/ops-base:latest ($(PLATFORM))"

build-ml:  ## ML base 이미지 빌드 (amd64, CUDA 12.6.3)
	docker buildx build --platform $(PLATFORM) --load \
	  -t local-claude/ops-ml:latest ops/docker/ml/
	@echo "✓ local-claude/ops-ml:latest ($(PLATFORM))"

build-all: build-base build-ml  ## 두 base 이미지 모두 빌드

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
