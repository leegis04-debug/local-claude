"""학습 진입점 — `make train` 으로 호출.

각 프로젝트는 src/models/ 의 모델을 학습하고 experiments/ 디렉터리에
실험 메타데이터(experiment.json)를 기록한다.
"""

import argparse
import json
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

    # TODO: 실제 학습 루프 구현
    metadata = {
        "exp_id": exp_id,
        "params": vars(args),
        "started_at": datetime.now().isoformat(),
        "status": "stub",
    }
    (exp_dir / "experiment.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    print(f"✓ stub experiment recorded: {exp_dir}/experiment.json")


if __name__ == "__main__":
    main()
