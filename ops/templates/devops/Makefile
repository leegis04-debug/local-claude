# DevOps Makefile — jw 트랙 솔루션 개발/배포 (Phase 5 §5.3)
# 회사 PC(100.92.202.196)에서 사용

PROJECT ?= $(shell basename $(CURDIR))
PYTHON ?= python3

# === 개발 ===
.PHONY: setup dev test lint

setup:  ## 가상환경 + 의존성 설치
	$(PYTHON) -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
	pre-commit install
	@echo "✓ Setup complete. Run: source .venv/bin/activate"

dev:  ## 로컬 개발 서버 실행
	docker compose -f docker-compose.dev.yml up --build

test:  ## 테스트 실행
	$(PYTHON) -m pytest tests/ -v --cov=src --cov-report=term-missing

lint:  ## 코드 품질 검사
	ruff check src/ tests/
	mypy src/ --ignore-missing-imports

# === 빌드 ===
.PHONY: build

build:  ## Docker 이미지 빌드
	docker build -t $(PROJECT):latest .

# === 배포 ===
.PHONY: deploy-staging deploy-prod rollback

deploy-staging:  ## Staging 배포 (4090 서버)
	@echo "Deploying to staging (4090)..."
	rsync -avz --exclude='.venv' --exclude='__pycache__' . ljw@100.105.221.243:~/services/$(PROJECT)/
	ssh ljw@100.105.221.243 'cd ~/services/$(PROJECT) && docker build -t $(PROJECT):staging . && docker stop $(PROJECT)-staging 2>/dev/null; docker run -d --rm --name $(PROJECT)-staging --gpus all -p 8090:8080 $(PROJECT):staging'
	@echo "✓ Staging: http://100.105.221.243:8090"

deploy-prod:  ## Production 배포 (수동 승인 필요)
	@echo "⚠ Production 배포: $(PROJECT)"
	@read -p "정말 배포하시겠습니까? (yes): " confirm && [ "$$confirm" = "yes" ] || exit 1
	rsync -avz --exclude='.venv' --exclude='__pycache__' . ljw@100.105.221.243:~/services/$(PROJECT)/
	ssh ljw@100.105.221.243 'cd ~/services/$(PROJECT) && docker build -t $(PROJECT):prod . && docker stop $(PROJECT)-prod 2>/dev/null; docker run -d --rm --name $(PROJECT)-prod --gpus all -p 8080:8080 $(PROJECT):prod'
	@echo "✓ Production: http://100.105.221.243:8080"

rollback:  ## 이전 버전으로 롤백
	ssh ljw@100.105.221.243 'docker stop $(PROJECT)-prod 2>/dev/null; docker run -d --rm --name $(PROJECT)-prod --gpus all -p 8080:8080 $(PROJECT):prod-prev'

# === 모니터링 ===
.PHONY: status logs

status:  ## 서비스 상태 확인
	@ssh ljw@100.105.221.243 'docker ps --filter name=$(PROJECT) --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"' 2>/dev/null || echo "SSH failed"

logs:  ## 서비스 로그 확인
	ssh ljw@100.105.221.243 'docker logs --tail 50 $(PROJECT)-prod' 2>/dev/null || echo "No prod container"

.PHONY: help
help:  ## 도움말
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
