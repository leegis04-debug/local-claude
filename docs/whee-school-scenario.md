# 휘스쿨 프로젝트 12주 시나리오

대학 기관 맞춤형 AI 영어학습 분석 플랫폼 — 통합 Ops 활용 흐름.

스택: `python,ml,java,pgvector,redis,frontend,observability,mlflow`

핵심 패턴 — **Walking Skeleton + Vertical Slice + Figma MCP**:
- **Week 1**: 가장 얇은 풀스택 1줄 통과 (통합 위험 조기 제거)
- **Week 2-7**: 백엔드/AI 깊이 채우기 (병렬 가능)
- **Week 8**: Figma MCP로 디자인 → React 자동 변환
- **Week 9-12**: 통합·배포·PoC

개발/배포 패턴:
- **개발**: M2 호스트 venv로 ai-worker 직접 실행 (Metal/MPS GPU 활용) +
  나머지(Spring/Frontend/DB/모니터링)는 Docker
- **배포**: amd64 이미지에 코드 포함 → GPU 서버로 이미지 자체 전송
  (CUDA 자동 감지, 어떤 서버든 동작)

---

## Day 0 — 프로젝트 초기화 (5분)

```bash
# 첫 1회 — base 이미지 빌드 (amd64 강제)
make -C ~/workspace/local-claude build-base
make -C ~/workspace/local-claude build-ml      # GPU 서버에서 빌드 권장

# 휘스쿨 프로젝트 생성
cd ~/Desktop/Doc/Dagyeom\ Inc/projects/
bash ~/workspace/local-claude/ops/project.sh new whee-school jw

# dev/ 스캐폴딩
bash ~/workspace/local-claude/ops/project.sh init-stack whee-school \
  --stack python,ml,java,pgvector,redis,frontend,observability,mlflow

cd whee-school/dev

# Antigravity (Figma MCP 사용)
antigravity .
```

---

## Week 1 — Walking Skeleton (얇은 풀스택 끝까지)

**목표**: "학생 1명 등록 → 조회" 만 풀스택으로 통과시키기. 이거 하나 통과하면
이후 풀스택 통합 위험 거의 제거됨.

```
Frontend (폼)
   ↓ POST /api/students
Spring Backend (JPA)
   ↓ INSERT
Postgres
   ↓ GET /api/students/{id}/predict
Spring → AI Worker (stub /infer/predict)
   ↓ 응답
Frontend (점수 표시)
```

### 1-1. 인프라 기동 (M2 GPU 활용)

```bash
# ai-worker 호스트 venv (M2 MPS 인식)
make ai-host-setup

# AI_WORKER_URL 호스트로
sed -i.bak 's|http://ai-worker:8001|http://host.docker.internal:8001|' .env.dev
rm .env.dev.bak

# Docker (ai-worker 제외)
make up-noai

# 별도 터미널에서 ai-worker 호스트 실행
make ai-host

# 검증
curl http://localhost:8001/info     # device: mps 확인
curl http://localhost:8080/actuator/health
curl http://localhost:3000
```

### 1-2. DB 스키마 (반나절)

`spring-backend/src/main/java/com/dagyeom/app/domain/`:
```
institution/InstitutionEntity.java   # id, name, code
student/StudentEntity.java           # id, institution_id, name, dept
```

`make backend-build && make restart` → `make db-shell`에서 테이블 생성 확인.

### 1-3. Spring API (반나절)

```java
@PostMapping("/api/students")
StudentResponse create(@RequestBody StudentRequest req) { ... }

@GetMapping("/api/students/{id}/predict")
PredictResponse predict(@PathVariable Long id) {
    var student = repo.findById(id).orElseThrow();
    var result = aiWorkerClient.inferPredict(toRequest(student));
    return new PredictResponse(student, result);
}
```

`AiWorkerClient`는 이미 스캐폴딩에 있음 (`ai/AiWorkerClient.java`).

### 1-4. AI Worker 호출 통과 (반나절)

`ai-worker/src/main.py`의 `/infer/predict` 는 이미 stub 응답 반환. 그대로 사용.

```bash
curl -X POST http://localhost:8080/api/students \
  -H "Content-Type: application/json" \
  -d '{"name":"홍길동","dept":"경영"}'
# → {"id":1,"name":"홍길동",...}

curl http://localhost:8080/api/students/1/predict
# → {"student":{...}, "ai":{"score":0.80,"extras":{...}}}
```

### 1-5. Frontend 폼 (1일, Figma 없이도 OK)

`frontend/src/app/page.tsx` 한 페이지에 등록 폼 + 점수 조회 버튼.

```tsx
'use client';
import { useState } from 'react';

export default function Home() {
  const [students, setStudents] = useState([]);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8080';
  // POST /api/students 후 GET /api/students/{id}/predict
  ...
}
```

### 1-6. 풀스택 통합 검증

브라우저 `http://localhost:3000` → 폼 입력 → 등록 → 점수 조회 → MPS 사용 확인.

**이 시점 산출물**: 동작하는 풀스택 1줄. 이제 살을 붙임.

---

## Week 2-3 — 백엔드 도메인 깊이

`spring-backend/`만 집중:

### Week 2

```
domain/
├── institution/   # 대학 기관 (멀티테넌트 root)
├── student/       # 학생 + 학과 + 학년
├── score/         # ToeicScoreEntity (시험 결과 시계열)
├── cohort/        # 학과·학기 코호트 view
└── auth/          # 학생/교수/교무처 RBAC
```

- `RbacConfig` + Spring Security 설정
- `Institution` 단위 멀티테넌트 (PostgreSQL RLS)
- 코호트 분석 service (`CohortAnalysisService.summarize(deptId, semester)`)

### Week 3

- **CSV ETL** (`POST /api/etl/scores`): Spring Batch 또는 단순 stream parser
- pgvector 스키마 초기화 (Spring AI auto)
- Repository 테스트 (TestContainers)

```bash
make test-java       # Gradle test
make backend-build && make restart
```

---

## Week 4-5 — AI Worker 본격 (Week 2-3 과 병렬 가능, M2 GPU 활용)

```bash
cd ai-worker
.venv/bin/python src/train.py --name baseline-xgb --epochs 100
make mlflow-ui       # 실험 추적
```

### Week 4

- **TOEIC 점수 예측 모델** (`src/models/predictor.py`):
  - XGBoost — 입력: 현재 점수, Part5 정답률, 남은 일수, 학습 참여율
  - 출력: 목표 점수 도달 확률 + ETA
- **학습 파이프라인** (`src/train.py`):
  - MLflow autolog 자동 활성화
  - 데이터 split (train/val/test)
  - 평가: MAE, R², AUC

### Week 5

- **ONNX export** (`make export-onnx CHECKPOINT=...`)
- `src/main.py /infer/predict` 스텁 → 실제 ONNX 추론으로 교체
  ```python
  import onnxruntime as ort
  sess = ort.InferenceSession("models/predictor.onnx", providers=RUNTIME["onnx_providers"])
  ```
- A/B 테스트 분석 (`scipy.stats.ttest_ind`, Cohen's d)
- 통계 분석 도구 정리 (`src/analytics/`)

검증:
```bash
curl -X POST http://localhost:8001/infer/predict \
  -d '{"data":{"current_score":580,"part5_acc":0.42,"days_left":60}}'
# → {"score": 0.85, "extras": {"eta_score": 720, "device": "mps"}}
```

---

## Week 6-7 — Spring AI Copilot + RAG (비즈니스 가치 핵심)

휘스쿨의 차별점.

### Week 6 — RAG 인프라

```bash
# HACCP/졸업인증 PDF → pgvector 임베딩
mkdir -p configs/policies
# (대학 매뉴얼 PDF 파일들 업로드)
make backend-shell
java -jar app.jar etl-rag --src /workspace/configs/policies/
exit
```

`spring-backend/.../ai/RagController.java`:
```java
@PostMapping("/api/rag/ask")
String ask(@RequestBody Question q) {
    var docs = vectorStore.similaritySearch(q.message());
    return chatClient.prompt()
        .system("다음 근거 기반 답변: " + docs)
        .user(q.message())
        .call().content();
}
```

### Week 7 — Copilot Tool Calling

```java
// CopilotController + 도구 정의
@Bean
List<FunctionCallback> tools(StudentService s, CohortService c) {
    return List.of(
        FunctionCallback.builder()
            .function("getRiskStudents", req -> s.findRiskStudents(req.deptId()))
            .description("학과별 졸업인증 미달 위험 학생 조회").build(),
        FunctionCallback.builder()
            .function("analyzeCohort", req -> c.analyze(req.deptId(), req.semester()))
            .description("코호트별 점수 변화 + 취약 파트 분석").build(),
        ...
    );
}
```

이제 시연 가능:
```
교무처: "이번 학기 경영학과 Part5 취약 학생 요약해줘"
↓
Spring AI ChatClient → analyzeCohort(deptId=경영) tool 호출
↓
DB 조회 + LLM 요약
↓
"경영학과 학생 142명 중 Part5 정답률 50% 이하 학생 38명...
 졸업인증 미달 위험 학생 12명 (학번/이름)..."
```

라이팅 자동 피드백도 같은 패턴:
```java
@PostMapping("/api/writing/feedback")
String feedback(@RequestBody Essay e) {
    return chatClient.prompt()
        .system("TOEIC 라이팅 채점 기준에 따라 피드백 생성. 강사 검수 가능한 초안 형식.")
        .user(e.text())
        .call().content();
}
```

---

## Week 8 — Frontend 본격 (Figma MCP 활용)

이때까지 Frontend는 Walking Skeleton 1페이지만. 이제 본격 채움.

### 8-1. Figma 디자인 준비 (디자이너 또는 본인)

Figma에서 휘스쿨 페이지 4종 디자인:
- 교무처 대시보드 (코호트 차트 + Copilot 채팅)
- 교수 학생 화면
- 학생 학습 플래너
- 라이팅 피드백 화면

각 페이지를 Frame으로 정리, 컴포넌트는 Variants로.

### 8-2. Figma MCP → Antigravity 연결

(셋업 명령은 시나리오 끝 "Figma MCP 부록" 참조)

Antigravity에서 자연어:
```
"Figma의 dashboard 페이지를 React/Next.js 컴포넌트로 변환해줘.
 - Tailwind 사용
 - src/app/(admin)/dashboard/ 에 page.tsx + components/
 - API 호출은 /api/cohort/summary, /api/copilot/ask
 - 토큰: 컬러/spacing 은 Figma variables 그대로"
```

→ Agent가 Figma MCP로 디자인 메타데이터 가져옴 → Tailwind 컴포넌트 생성 →
저장 시 frontend HMR 자동 반영.

### 8-3. 페이지 구조

```
frontend/src/app/
├── page.tsx                          # 랜딩 (역할 선택)
├── (admin)/
│   ├── dashboard/page.tsx           # 코호트 차트 + Copilot 채팅
│   └── reports/page.tsx             # 자동 리포트 목록
├── (faculty)/
│   └── students/page.tsx            # 본인 강의 학생
├── (student)/
│   ├── me/page.tsx                  # 본인 점수 + 플래너
│   └── writing/page.tsx             # 라이팅 + AI 피드백
└── components/
    ├── CohortChart.tsx
    ├── ChatCopilot.tsx              # Spring AI ChatController 호출
    └── StudentTable.tsx
```

### 8-4. 검증

```bash
make fe-logs         # http://localhost:3000
# → Antigravity 내장 브라우저에서 직접 확인
```

---

## Week 9-10 — 통합·배포

### Week 9 — Observability + 보안

```bash
make obs-ui          # Grafana 대시보드 점검
```

추가 작업:
- Spring Security RLS 검증 (멀티테넌트 분리)
- JWT 인증 (학생/교수/교무처 토큰 분리)
- Grafana 알림 (Copilot 응답 지연 / 5xx 비율)

### Week 10 — GPU 서버 배포 (이미지 자체 전송)

```bash
# .env.prod 채우기
cp .env.prod.template .env.prod
vim .env.prod        # POSTGRES_PASSWORD, ANTHROPIC_API_KEY, GRAFANA_PASSWORD

# amd64 이미지 빌드 (코드 포함)
make build-images

# GPU 서버로 이미지+compose 전송 + 자동 실행
make ship REMOTE=ljw@<gpu-server>

# 검증 (자동 GPU 감지)
curl http://<gpu-server>:8001/info
# → {"device":"cuda","gpu_name":"...","onnx_providers":["CUDAExecutionProvider", ...]}
```

CI도 자동 (`git push origin main` → GitHub Actions).

---

## Week 11-12 — 첫 대학 PoC + 피드백

```bash
# 학생 라이팅 피드백 수집 (강사 검수 큐)
# spring-backend/.../writing/ReviewQueueController.java

# 운영 메트릭 모니터링
ssh ljw@<gpu-server> "docker compose -f ~/services/whee-school/docker-compose.prod.yml logs -f"

# DB 백업
ssh ljw@<gpu-server> 'cd ~/services/whee-school && \
  docker compose exec postgres pg_dump -U dev app > /tmp/scores-backup.sql'
```

피드백 수집 → 다음 Phase 결정.

---

## Phase 2 — K8s 마이그레이션 (트래픽 증가 시)

```bash
bash ~/workspace/local-claude/ops/project.sh init-stack whee-school \
  --stack python,ml,java,pgvector,redis,frontend,observability,mlflow,k8s

make k8s-template > k8s/preview.yaml
make k8s-install
```

---

## 핵심 흐름 요약

| 주차 | 단계 | 산출물 |
|------|------|--------|
| **W1** | Walking Skeleton | 풀스택 1줄 통과 (등록→점수 조회) |
| **W2-3** | 백엔드 도메인 | RBAC + RLS + ETL + 코호트 |
| **W4-5** | AI Worker (병렬 OK) | XGBoost 학습 + ONNX export + MLflow |
| **W6-7** | Copilot + RAG | Tool Calling + pgvector + 라이팅 피드백 |
| **W8** | Frontend (Figma MCP) | 4페이지 풀 디자인 |
| **W9-10** | 통합·배포 | Observability + GPU 서버 ship |
| **W11-12** | PoC | 첫 대학 운영 + 피드백 |

**한 줄 정리**: Week 1 Walking Skeleton 으로 통합 위험 제거 → 이후 백엔드/AI/UI를
세로 슬라이스로 채움 → Figma MCP가 8주차 UI 작업 가속.

---

## 부록 A — Figma MCP 셋업

Antigravity (또는 Claude Desktop) 에 Figma MCP 서버를 등록하면 IDE 안에서
"Figma의 X 페이지를 React로 변환해줘" 같은 자연어 명령이 동작.

### A-1. Figma 측 준비

**옵션 1 — Figma 공식 Dev Mode MCP** (Pro/Org/Enterprise):
1. Figma Desktop 앱 열기
2. Preferences → Dev Mode MCP Server 토글 ON
3. 서버가 `http://127.0.0.1:3845/mcp` 에 떠 있게 됨

**옵션 2 — Thirdparty figma-developer-mcp** (Free, 토큰 기반):
1. Figma → Settings → Personal Access Tokens → Create new
2. 토큰을 `~/.config/figma/.env` 에 저장:
   ```
   FIGMA_API_KEY=figd_...
   ```

### A-2. Antigravity 측 등록

`~/Library/Application Support/Antigravity/User/mcp.json`
(또는 Antigravity Settings → MCP Servers UI):

```json
{
  "mcpServers": {
    "figma": {
      "command": "npx",
      "args": ["-y", "figma-developer-mcp", "--stdio"],
      "env": {
        "FIGMA_API_KEY": "figd_..."
      }
    }
  }
}
```

또는 공식 Dev Mode 사용 시:

```json
{
  "mcpServers": {
    "figma-dev-mode": {
      "url": "http://127.0.0.1:3845/mcp"
    }
  }
}
```

Antigravity 재시작.

### A-3. 사용 방법

Antigravity 채팅에서:

```
"Figma 파일 https://figma.com/file/ABC123 의 dashboard 프레임을
 frontend/src/app/(admin)/dashboard/page.tsx 로 변환해줘.
 - Tailwind 클래스
 - 기존 CohortChart, ChatCopilot 컴포넌트 재사용
 - API 호출: /api/cohort/summary, /api/copilot/ask"
```

→ Agent가 Figma MCP로 프레임 메타데이터(레이아웃, 컬러, spacing, text style) 조회 →
Next.js 컴포넌트 생성 → 저장 시 HMR 반영.

### A-4. 디자인 토큰 동기화 (선택)

Figma의 컬러/spacing variables를 `tailwind.config.js` 에 자동 반영:

```
"Figma variables 를 tailwind.config.js theme.extend.colors / spacing 으로 export"
```

→ 디자인 변경 시 자동으로 UI 토큰 업데이트.

---

## 참고: 두 가지 ai-worker 실행 모드

| 모드 | 명령 | 장점 | 단점 |
|------|------|------|------|
| **호스트 venv** (개발용 권장) | `make ai-host-setup` + `make ai-host` | M2 MPS GPU 활용, 빠른 hot-reload | Docker 아님 |
| **Docker 컨테이너** (검증용) | `make up` (전체) | GPU 서버와 동일 환경 | M2 GPU 못 씀 (Docker for Mac) |

전환은 `.env.dev` 의 `AI_WORKER_URL` 한 줄:
- 호스트: `http://host.docker.internal:8001`
- Docker: `http://ai-worker:8001`

---

## 다음 단계 후보

- Spring AI Tool Calling 풀 구현 (학과·코호트·취약 학생 도구)
- ML 학습 파이프라인 (XGBoost + MLflow autolog 실전 코드)
- 첫 대학 데이터 받았을 때 ETL 절차 (CSV → JPA → pgvector RAG)
- RBAC + PostgreSQL RLS 멀티테넌트 구현
- 라이팅 자동 피드백 (Spring AI ChatClient + 강사 검수 큐)
- Figma 컴포넌트 라이브러리 → Storybook 자동 생성
