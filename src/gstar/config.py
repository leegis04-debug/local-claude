"""G 런타임 경로와 기본 설정.

데이터 디렉토리: $GSTAR_HOME (기본 ~/.gstar).
weights 는 $GSTAR_HOME/config.toml [weights] 블록으로 오버라이드.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    home: Path
    db: Path
    faiss: Path
    config: Path

    @classmethod
    def load(cls) -> "Paths":
        home = Path(os.environ.get("GSTAR_HOME", Path.home() / ".gstar"))
        state = home / "state"
        return cls(
            home=home,
            db=state / "g.duckdb",
            faiss=state / "emb.faiss",
            config=home / "config.toml",
        )

    def ensure(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.db.parent.mkdir(parents=True, exist_ok=True)


EMBED_DIM_DEFAULT = 384  # paraphrase-multilingual-MiniLM-L12-v2


@dataclass(frozen=True)
class Weights:
    """중력 점수 가중치. 합이 1.0 일 필요는 없다 (정규화 없음)."""
    w_rel: float = 0.30
    w_rec: float = 0.10
    w_cent: float = 0.20
    w_ver: float = 0.10
    w_pur: float = 0.20
    w_stab: float = 0.10
    halflife_days: float = 30.0
    seed_k: int = 5              # FAISS top-K 를 centrality seed 로
    candidate_k: int = 200       # gravity 점수 계산 대상 후보 수

    @classmethod
    def load(cls, config_path: Path) -> "Weights":
        if not config_path.exists():
            return cls()
        with config_path.open("rb") as f:
            data = tomllib.load(f)
        section = data.get("weights", {})
        known = {fld.name for fld in fields(cls)}
        kwargs = {k: v for k, v in section.items() if k in known}
        return cls(**kwargs)
