"""임베딩 추상화.

Embedder 프로토콜만 노출. 구현은 sbert.py / ollama.py (향후).
테스트에서는 DummyEmbedder 를 주입한다.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class Embedder(Protocol):
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray: ...  # shape (N, dim), float32


class DummyEmbedder:
    """해시 기반 결정적 가짜 임베딩. 테스트 전용."""

    def __init__(self, dim: int = 16):
        self.dim = dim

    def encode(self, texts: list[str]) -> np.ndarray:
        rng_seed = [abs(hash(t)) % (2**32) for t in texts]
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, seed in enumerate(rng_seed):
            rng = np.random.default_rng(seed)
            out[i] = rng.standard_normal(self.dim).astype(np.float32)
        return out
