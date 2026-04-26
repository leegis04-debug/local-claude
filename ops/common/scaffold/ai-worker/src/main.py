"""AI Worker — Spring Backend 가 호출하는 비전/시계열 추론 서버.

표준 인터페이스 (모든 프로젝트 공통):
- POST /infer/grade    : 분류 (예: ResNet18 숙성등급)
- POST /infer/anomaly  : 이상탐지 (예: PaDiM/PatchCore)
- POST /infer/predict  : 시계열/회귀 (예: LSTM/XGBoost)
- GET  /health         : 헬스체크

각 프로젝트는 src/models/ 에 실제 모델 구현을 채우고 아래 stub 을 교체한다.
"""

from fastapi import FastAPI
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator

app = FastAPI(title="AI Worker", version="0.1.0")

# Prometheus /metrics 엔드포인트 자동 노출 (HTTP 지연/카운트 자동 수집)
Instrumentator().instrument(app).expose(app)


class InferRequest(BaseModel):
    image_path: str | None = None
    data: dict | None = None


class InferResult(BaseModel):
    label: str | None = None
    score: float | None = None
    extras: dict | None = None


@app.post("/infer/grade", response_model=InferResult)
async def infer_grade(req: InferRequest) -> InferResult:
    # TODO: src/models/grader.py 구현 후 교체
    return InferResult(label="A", score=0.95, extras={"stub": True})


@app.post("/infer/anomaly", response_model=InferResult)
async def infer_anomaly(req: InferRequest) -> InferResult:
    # TODO: src/models/anomaly.py 구현 후 교체
    return InferResult(score=0.12, extras={"heatmap_path": None, "stub": True})


@app.post("/infer/predict", response_model=InferResult)
async def infer_predict(req: InferRequest) -> InferResult:
    # TODO: src/models/predictor.py 구현 후 교체
    return InferResult(score=0.80, extras={"eta_hours": 48, "stub": True})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
