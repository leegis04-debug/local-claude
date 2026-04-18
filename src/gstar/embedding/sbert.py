"""sentence-transformers 기반 Embedder. 한국어·영어 멀티링구얼 기본."""

from __future__ import annotations

import numpy as np

_DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class SBertEmbedder:
    """모델 로드는 lazy. 최초 encode 시 ~80MB 다운로드."""

    def __init__(self, model_name: str = _DEFAULT_MODEL):
        self.model_name = model_name
        self._model = None
        self._dim: int | None = None

    def _ensure(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)
        # sentence-transformers v5 에서 이름이 바뀜. 구버전 호환 유지.
        if hasattr(self._model, "get_embedding_dimension"):
            self._dim = int(self._model.get_embedding_dimension())
        else:
            self._dim = int(self._model.get_sentence_embedding_dimension())

    @property
    def dim(self) -> int:
        self._ensure()
        assert self._dim is not None
        return self._dim

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        self._ensure()
        assert self._model is not None
        vecs = self._model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
        return vecs.astype(np.float32)
