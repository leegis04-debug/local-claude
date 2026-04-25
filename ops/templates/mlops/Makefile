# MLOps Makefile — re 트랙 실험 환경 (Phase 5 §5.2)
# 회사 PC(100.92.202.196)에서 사용

PROJECT ?= $(shell basename $(CURDIR))
PYTHON ?= python3
PYTHONPATH := $(HOME)/lib:$(PYTHONPATH)
export PYTHONPATH

# === 개발 ===
.PHONY: setup train eval test lint

setup:  ## 가상환경 + 의존성 설치
	$(PYTHON) -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	@echo "✓ Setup complete. Run: source .venv/bin/activate"

train:  ## 학습 실행 (lab_tracker 자동 기록)
	$(PYTHON) src/train.py $(ARGS)

eval:  ## 평가 실행
	$(PYTHON) src/eval.py $(ARGS)

test:  ## 테스트 실행
	$(PYTHON) -m pytest tests/ -v

lint:  ## 코드 품질 검사
	ruff check src/ tests/
	mypy src/ --ignore-missing-imports

# === 실험 관리 ===
.PHONY: experiments sync lab-note

experiments:  ## 실험 목록 보기
	@ls -lt experiments/*/experiment.json 2>/dev/null | head -10 || echo "No experiments yet"

sync:  ## 실험 결과 NAS 동기화 (수동)
	@if [ -z "$$LAB_TRACKER_NAS_TARGET" ]; then echo "LAB_TRACKER_NAS_TARGET not set"; exit 1; fi
	rsync -avz experiments/ $$LAB_TRACKER_NAS_TARGET$(PROJECT)/experiments/

# === Docker ===
.PHONY: docker-build docker-train

docker-build:  ## Docker 이미지 빌드
	docker build -t $(PROJECT):latest .

docker-train:  ## Docker 컨테이너에서 학습
	docker run --rm --gpus all -v $(CURDIR):/workspace -w /workspace $(PROJECT):latest make train ARGS="$(ARGS)"

.PHONY: help
help:  ## 도움말
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
