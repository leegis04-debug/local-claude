"""PyTorch 체크포인트 → ONNX 변환 헬퍼.

각 프로젝트는 load_model() 을 자기 모델로 교체한다.
"""

import argparse
from pathlib import Path


def load_model(checkpoint: str):
    """프로젝트별 모델 로드 — 교체 필요."""
    import torch
    # TODO: 실제 모델 클래스 import 후 state_dict 로드
    # 예: from models.grader import Grader; m = Grader(); m.load_state_dict(torch.load(checkpoint)); m.eval()
    return torch.nn.Linear(3, 1).eval()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", default="models/model.onnx")
    parser.add_argument("--input-shape", default="1,3,224,224", help="comma-separated, e.g. 1,3,224,224")
    args = parser.parse_args()

    import torch

    model = load_model(args.checkpoint)
    shape = tuple(int(x) for x in args.input_shape.split(","))
    dummy = torch.randn(*shape)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        dummy,
        args.output,
        input_names=["input"],
        output_names=["output"],
        dynamic_axes={"input": {0: "batch"}, "output": {0: "batch"}},
        opset_version=17,
    )

    # 검증
    import onnx
    onnx.checker.check_model(onnx.load(args.output))
    print(f"✓ ONNX export ok: {args.output}")


if __name__ == "__main__":
    main()
