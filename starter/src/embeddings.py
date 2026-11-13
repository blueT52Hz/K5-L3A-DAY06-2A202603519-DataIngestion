"""Real local embedding model via sentence-transformers (no API key, cost 0).

The same model, prefixes and normalization are used for all strategies.
If the model cannot be loaded the error is raised; there is no mock fallback.
"""
from __future__ import annotations

from typing import Protocol

from sentence_transformers import SentenceTransformer


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, question: str) -> list[float]: ...


class LocalE5Embedder:
    """intfloat/multilingual-e5-* expects 'passage: ' / 'query: ' prefixes."""

    def __init__(self, model_name: str, batch_size: int = 32, device: str = "cpu") -> None:
        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device=device)
        get_dim = getattr(self.model, "get_embedding_dimension", None) or \
            self.model.get_sentence_embedding_dimension
        self.dimensions = int(get_dim())
        self.batch_size = batch_size
        self.use_prefix = "e5" in model_name.lower()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.use_prefix:
            texts = [f"passage: {t}" for t in texts]
        vecs = self.model.encode(texts, batch_size=self.batch_size,
                                 normalize_embeddings=True, show_progress_bar=False)
        return vecs.tolist()

    def embed_query(self, question: str) -> list[float]:
        text = f"query: {question}" if self.use_prefix else question
        return self.model.encode([text], normalize_embeddings=True)[0].tolist()
