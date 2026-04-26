"""AI Worker — Spring Backend 가 호출하는 비전/시계열 추론 서버.

표준 인터페이스 (모든 프로젝트 공통):
- POST /infer/grade    : 분류 (예: ResNet18 숙성등급)
- POST /infer/anomaly  : 이상탐지 (예: PaDiM/PatchCore)
- POST /infer/predict  : 시계열/회귀 (예: LSTM/XGBoost)
- GET  /health         : 헬스체크
- GET  /info           : 런타임 정보 (DEVICE/providers/이미지 메타)

이 컨테이너는 어떤 호스트에서든 동작:
- GPU 있으면 자동으로 CUDA 사용
- 없으면 CPU 폴백 (Apple Silicon/x86 CPU/임의 서버)

각 프로젝트는 src/models/ 에 실제 모델 구현을 채우고 아래 stub 을 교체한다.
"""

import logging
import os

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

log = logging.getLogger("ai-worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")

# ── 런타임 자동 감지 (호스트 무관) ───────────────────────────
def _detect_runtime() -> dict:
    info: dict = {"image": os.environ.get("APP_VERSION", "dev")}

    # PyTorch — CUDA / MPS / CPU 우선순위 자동 선택
    try:
        import torch
        info["torch"] = torch.__version__
        if torch.cuda.is_available():
            info["device"] = "cuda"
            info["gpu_name"] = torch.cuda.get_device_name(0)
            info["gpu_count"] = torch.cuda.device_count()
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            info["device"] = "mps"  # Apple Silicon (M1/M2/M3) — 호스트 venv 실행 시만
            info["gpu_name"] = "Apple Metal"
        else:
            info["device"] = "cpu"
    except ImportError:
        info["torch"] = None
        info["device"] = "cpu"

    # ONNX Runtime — providers 자동 우선순위
    try:
        import onnxruntime as ort
        avail = ort.get_available_providers()
        providers = []
        if "CUDAExecutionProvider" in avail:
            providers.append("CUDAExecutionProvider")
        if "CoreMLExecutionProvider" in avail:  # Apple Silicon
            providers.append("CoreMLExecutionProvider")
        providers.append("CPUExecutionProvider")
        info["onnx_providers"] = providers
        info["onnx_version"] = ort.__version__
    except ImportError:
        info["onnx_providers"] = []

    return info


RUNTIME = _detect_runtime()
log.info("Runtime detected: %s", RUNTIME)


# ── FastAPI ───────────────────────────────────────────────
app = FastAPI(title="AI Worker", version=RUNTIME.get("image", "dev"))
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
    return InferResult(label="A", score=0.95, extras={"stub": True, "device": RUNTIME["device"]})


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


@app.get("/info")
async def info() -> dict:
    """배포된 환경의 런타임 정보 — 어떤 서버에 떠 있는지 확인용."""
    return RUNTIME
