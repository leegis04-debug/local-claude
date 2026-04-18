"""경로·설정 로드."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    home: Path
    current: Path
    contexts_dir: Path
    history: Path
    device: Path

    @classmethod
    def load(cls) -> "Paths":
        home = Path(os.environ.get("CTX_HOME", Path.home() / ".ctx"))
        return cls(
            home=home,
            current=home / "current",
            contexts_dir=home / "contexts",
            history=home / "history.jsonl",
            device=home / "device.json",
        )

    def ensure(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.contexts_dir.mkdir(parents=True, exist_ok=True)
