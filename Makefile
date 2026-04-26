# local-claude root Makefile — 공통 base 이미지 빌드
#
# 처음 한 번만 실행:
#   make build-base    # ubuntu:22.04 + python3.12 + gcc/cmake (~700MB)
#   make build-ml      # nvidia/cuda:12.4.1 + opencv 런타임 (~6GB, GPU 머신만)
#
# 각 프로젝트 dev/ 의 Dockerfile 은 위 이미지를 FROM 으로 참조한다.

.PHONY: build-base build-ml build-all help

build-base:  ## 공통 base 이미지 빌드 (python3.12 + gcc/cmake)
	docker build -t local-claude/ops-base:latest ops/docker/base/
	@echo "✓ local-claude/ops-base:latest"

build-ml:  ## ML base 이미지 빌드 (CUDA 12.4.1 + OpenCV 런타임)
	docker build -t local-claude/ops-ml:latest ops/docker/ml/
	@echo "✓ local-claude/ops-ml:latest"

build-all: build-base build-ml  ## 두 base 이미지 모두 빌드

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
