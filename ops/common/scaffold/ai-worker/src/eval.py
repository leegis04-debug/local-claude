"""평가 진입점 — `make eval` 으로 호출."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=False)
    args = parser.parse_args()

    # TODO: 실제 평가 로직 구현
    print(f"eval stub (checkpoint={args.checkpoint})")


if __name__ == "__main__":
    main()
