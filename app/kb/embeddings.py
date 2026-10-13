"""Text embedders: Vertex AI in production, a deterministic local hashing embedder offline."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Literal, Protocol

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]

_STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can", "could", "do", "does", "for", "from", "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on", "or", "our", "so", "than", "that", "the", "their", "them", "then", "there", "these", "they", "this", "to", "too", "us", "was", "we", "were", "what", "when", "where", "which", "while", "who", "why", "will", "with", "would", "you", "your", "much", "many"]
)
_SUFFIXES = ("ations", "ation", "ings", "ing", "als", "al", "ers", "er", "ed", "es", "s", "e")


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS]


class Embedder(Protocol):
    model_name: str
    dim: int
    # Cosine scale differs by model; KB_MIN_SCORE overrides this when set.
    recommended_min_score: float

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Offline stand-in: signed feature hashing of stemmed tokens, weighted by IDF fitted on the KB at ingest."""

    recommended_min_score = 0.15

    def __init__(self, dim: int, idf: dict[str, float]) -> None:
        self.dim = dim
        self.idf = idf
        self._unseen_idf = max(idf.values(), default=1.0)
        self.model_name = f"local-hashing-idf-v2-{dim}"

    @classmethod
    def fit(cls, texts: list[str], dim: int) -> HashingEmbedder:
        df = Counter(token for text in texts for token in set(tokenize(text)))
        n = len(texts)
        return cls(dim, {t: round(math.log((n + 1) / (c + 1)) + 1.0, 6) for t, c in sorted(df.items())})

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token, count in Counter(tokenize(text)).items():
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[index] += sign * (1.0 + math.log(count)) * self.idf.get(token, self._unseen_idf)
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class VertexEmbedder:
    _BATCH = 50
    recommended_min_score = 0.35

    def __init__(self, project: str, location: str, model: str, dim: int) -> None:
        if not model:
            raise ValueError("EMBEDDING_MODEL must be set when LLM_BACKEND=vertex")
        from google import genai

        self._client = genai.Client(vertexai=True, project=project or None, location=location)
        self.model_name = model
        self.dim = dim

    async def _embed(self, texts: list[str], task: TaskType) -> list[list[float]]:
        from google.genai import types

        out: list[list[float]] = []
        for start in range(0, len(texts), self._BATCH):
            resp = await self._client.aio.models.embed_content(
                model=self.model_name,
                contents=texts[start:start + self._BATCH],
                config=types.EmbedContentConfig(task_type=task, output_dimensionality=self.dim),
            )
            out.extend(list(e.values or []) for e in resp.embeddings or [])
        return out

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "RETRIEVAL_QUERY"))[0]
