# 06. 실험 프로토콜 — {{JIRA_KEY}}

> 입력: `04-spec.md`, `05-risk.md`
> 스타일: waterjet_detector `DEV_PLAN.md` (Step 단위 분해 + 주요 기능 + 파일 경로)

---

## Step 1: [단계 제목]
- **목표**:
- **주요 기능**:
    - 기능 1
    - 기능 2
- **관련 파일**:
    - `model/<name>/torch_model.py`
    - `scripts/<...>.py`
- **완료 기준**: (측정 가능한 Done 조건)
- **예상 소요**: ?일 (담당: {{ASSIGNEE}})

## Step 2: [단계 제목]
- **목표**:
- **주요 기능**:
- **관련 파일**:
- **완료 기준**:
- **예상 소요**:

## Step 3: 테스트 코드 작성 및 검증
- **목표**: 시각화 결과·수치가 의도한 기준에 맞게 생성되는지 확인
- **파일**: `test/step_N_test.py`
- **완료 기준**: pytest 통과

## Step 4: [통합·데이터셋 연동]
- **목표**:
- **주요 기능**:
- **관련 파일**:
- **완료 기준**:

## Step 5: [선택 — 성능 개선·Deep 버전]
- **목표**:
- **관련 파일** (waterjet_detector plus 스타일 참조):
    - `model/<name>_plus/config.py`
    - `model/<name>_plus/torch_model.py`
    - `model/<name>_plus/lightning_model.py`
    - `model/<name>_plus/__init__.py`
- **완료 기준**:

## Step 6: [통합 평가·시각화]
- **목표**:
- **주요 기능**:
    - backbone_attn · spatial_prior 결합 시각화 등
- **파일**: `scripts/evaluate_and_visualize.py`
- **완료 기준**:

---

## 재현성·코드 규칙
- Google Style Docstring 준수
- OOP 기반 클래스화
- 모든 CLI는 `eyekit-ai run <project> <script> -- [ARGS...]` 로 실행 가능
- 실험 결과는 `dev/research-notes/daily/YYYY-MM-DD.md` 에 수치 표로 기록

## 실험 Run 체계
| Run ID | 조건 | 지표 | 결과 | 체크포인트 경로 |
|--------|------|------|------|----------------|
| run-01 |  |  |  |  |

---
> 다음: `07-instruction.md` (최종 연구지시서)
