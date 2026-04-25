# 04. 기술명세 — {{JIRA_KEY}}

> 입력: `03-structure.md`
> 기반: **EyekitAI 프레임워크** (`00-input/BASE/EyekitAI-main/`)
> 참조 스타일: **waterjet_detector** (`00-input/BASE/research_model-waterjet_detector/`)

---

## 1. 작업 범위 (Scope)
### 포함
-

### 제외 (Out of Scope)
-

## 2. EyekitAI 프로젝트 구성
> 본 Task는 EyekitAI 기반 프로젝트에 통합됩니다. 신규 생성 또는 기존 프로젝트 확장 여부 명시.

| 항목 | 내용 |
|------|------|
| 프로젝트 타입 | `setup-research` / `setup-triton` |
| 프로젝트명 | (예: deep_tect_vision) |
| 신규 추가 모델 | (예: `sam2_region_extractor`) |
| 신규 추가 데이터셋 | (예: `wafer_cleaning_frames`) |
| 신규 추가 Config | (예: `config/model/sam2_region_extractor/*.yaml`) |
| 기존 재사용 | (예: waterjet_detector의 spatial_prior 로직) |

## 3. 모델/데이터셋 명세
### 3-1. 모델 (model/<name>/)
```python
# lightning_model.py (BasePLModel 상속)
@MODEL_REGISTRY.register(name="<name>")
class <Name>LightningModel(BasePLModel):
    ...

# torch_model.py — Triton 입출력 스펙 필수
@triton_pytorch_model(name="<name>_pt", max_batch_size=...)
@triton_input(name="input", shape=(-1, ...), dtype=torch....)
@triton_output(name="output", shape=(-1, ...), dtype=torch....)
class <Name>TorchModel(BaseTorchModel):
    ...
```

### 3-2. 데이터셋 (dataset/<name>/)
- `dataset.py` — `BaseEyekitDataset` 상속
- `_load_data()` 반환: `[(image_path, label), ...]`
- 전처리·증강:

### 3-3. Config (config/)
- 모델 config: `config/model/<name>/<name>.yaml`
- 데이터 config: `config/dataset/<name>/<name>.yaml`
- 트레이너 config: `config/trainer_config.yaml`

## 4. 주요 수식·알고리즘
```
(필요 시 수식 기재)
```

## 5. 입출력 인터페이스
| 이름 | 형태 | 타입 | 설명 |
|------|------|------|------|
| input |  |  |  |
| output |  |  |  |

## 6. 구현 난이도 (다겸 팀 기준)
| 구성요소 | 난이도(상/중/하) | 근거 |
|----------|-----------------|------|
|  |  |  |

## 7. 의존성
- Python, PyTorch, CUDA 버전:
- 추가 라이브러리:
- 선행 Jira Task:

## 8. CLI 실행 예시
```bash
# 프로젝트 초기화 (해당하는 경우)
eyekit-ai setup-research --name deep_tect_vision --model <name> --dataset <name>

# 학습
eyekit-ai train deep_tect_vision <name>

# 검증
eyekit-ai test deep_tect_vision <name>
```

---
> 다음: `05-risk.md` (리스크 점검)
