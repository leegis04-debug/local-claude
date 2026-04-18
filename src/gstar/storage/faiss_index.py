"""FAISS 임베딩 인덱스. cosine 유사도용 IndexFlatIP + L2 정규화."""

from __future__ import annotations

from pathlib import Path

import faiss
import numpy as np


class FaissStore:
    """id ↔ row 매핑은 병렬 .npy 로 보존. 규모가 커지면 IVF 로 교체."""

    def __init__(self, index_path: Path, dim: int):
        self.index_path = Path(index_path)
        self.ids_path = self.index_path.with_suffix(".ids.npy")
        self.dim = dim
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        if self.index_path.exists() and self.ids_path.exists():
            self.index = faiss.read_index(str(self.index_path))
            self.ids = list(np.load(self.ids_path, allow_pickle=True))
        else:
            self.index = faiss.IndexFlatIP(dim)
            self.ids = []

    def __len__(self) -> int:
        return self.index.ntotal

    def add(self, node_id: str, vector: np.ndarray) -> None:
        vec = _normalize(vector.astype(np.float32).reshape(1, -1))
        self.index.add(vec)
        self.ids.append(node_id)

    def add_batch(self, node_ids: list[str], vectors: np.ndarray) -> None:
        if len(node_ids) != vectors.shape[0]:
            raise ValueError("node_ids length must match vectors rows")
        vec = _normalize(vectors.astype(np.float32))
        self.index.add(vec)
        self.ids.extend(node_ids)

    def search(self, vector: np.ndarray, k: int) -> list[tuple[str, float]]:
        if self.index.ntotal == 0:
            return []
        q = _normalize(vector.astype(np.float32).reshape(1, -1))
        k = min(k, self.index.ntotal)
        scores, idxs = self.index.search(q, k)
        results: list[tuple[str, float]] = []
        for score, idx in zip(scores[0], idxs[0]):
            if idx < 0:
                continue
            results.append((self.ids[idx], float(score)))
        return results

    def save(self) -> None:
        faiss.write_index(self.index, str(self.index_path))
        np.save(self.ids_path, np.array(self.ids, dtype=object), allow_pickle=True)


def _normalize(vec: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vec, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vec / norms
