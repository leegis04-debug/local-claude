"""학습 진입점 — `make train` 으로 호출.

MLflow Tracking 자동 로깅 + 로컬 experiments/ JSON 백업.
MLFLOW_TRACKING_URI 환경변수가 있으면 MLflow 서버로 송신,
없으면 로컬 파일만 기록.
"""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="exp")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--lr", type=float, default=1e-3)
    args = parser.parse_args()

    exp_id = f"{args.name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    exp_dir = Path("experiments") / exp_id
    exp_dir.mkdir(parents=True, exist_ok=True)

    # MLflow Tracking (선택) — TRACKING_URI 있으면 자동 활성화
    use_mlflow = bool(os.environ.get("MLFLOW_TRACKING_URI"))
    mlflow = None
    if use_mlflow:
        import mlflow as _mlflow
        mlflow = _mlflow
        mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])
        mlflow.set_experiment(args.name)
        # PyTorch / sklearn / xgboost 등 자동 로깅 (감지된 라이브러리 한정)
        try:
            mlflow.autolog()
        except Exception:
            pass
        mlflow.start_run(run_name=exp_id)
        mlflow.log_params(vars(args))

    # TODO: 실제 학습 루프
    for epoch in range(args.epochs):
        train_loss = 1.0 - epoch * 0.05
        if use_mlflow and mlflow is not None:
            mlflow.log_metric("train_loss", train_loss, step=epoch)

    metadata = {
        "exp_id": exp_id,
        "params": vars(args),
        "started_at": datetime.now().isoformat(),
        "mlflow": use_mlflow,
        "status": "stub",
    }
    (exp_dir / "experiment.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False)
    )

    if use_mlflow and mlflow is not None:
        mlflow.end_run()

    print(f"✓ experiment recorded: {exp_dir}/experiment.json (mlflow={use_mlflow})")


if __name__ == "__main__":
    main()
