from __future__ import annotations

from typing import Protocol

import numpy as np
from sklearn.decomposition import PCA

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
_model = None


class TextEncoder(Protocol):
    def encode(self, texts: list[str], show_progress_bar: bool = False) -> np.ndarray: ...


def _get_model() -> TextEncoder:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        # Auto-selects CUDA if a GPU + CUDA-enabled torch build are present,
        # falls back to CPU otherwise — no code path difference either way.
        _model = SentenceTransformer(_MODEL_NAME)
    return _model


def embed_texts(texts: list[str], model: TextEncoder | None = None) -> np.ndarray:
    """Sentence embeddings for filing text.

    Empty strings (a section that couldn't be extracted) embed to an
    all-zero vector rather than crashing the batch or being skipped and
    misaligning the output with the input.
    """
    model = model or _get_model()
    placeholders = [t if t else " " for t in texts]
    vectors = np.asarray(model.encode(placeholders, show_progress_bar=False))
    for i, t in enumerate(texts):
        if not t:
            vectors[i] = 0.0
    return vectors


def fit_reducer(vectors: np.ndarray, n_components: int = 16) -> PCA:
    """Fit PCA on embeddings from the training split only.

    Fitting on the full dataset (train+test) would leak test-time text
    structure into the projection used at training time.
    """
    n_components = max(1, min(n_components, vectors.shape[0], vectors.shape[1]))
    pca = PCA(n_components=n_components, random_state=42)
    pca.fit(vectors)
    return pca


def reduce_vectors(pca: PCA, vectors: np.ndarray) -> np.ndarray:
    return pca.transform(vectors)
