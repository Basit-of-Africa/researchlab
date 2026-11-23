"""Embedding providers (fixed 768 dimensions to match the pgvector column).

``hashing`` is a dependency-free, deterministic *lexical* embedding (signed
feature hashing of word unigrams/bigrams and character trigrams). It is always
available and keeps the system functional at zero cost, but it is not a
semantic model; configure ``sentence_transformers``, ``ollama`` or ``gemini``
for semantic retrieval. Embeddings are stored with their model name so mixed
models are never compared.
"""
from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

import httpx
import numpy as np

from app.config import get_settings

DIM = 768
_WORD = re.compile(r"[a-z0-9][a-z0-9\-']+")
_STOP = set(
    "the a an and or of to in on for with by from at as is are was were be been being this that these those it its "
    "into than then there their which who whom whose what when where how not no but if also such can may might "
    "will would should could has have had do does did we our you your they them he she his her i".split()
)


class EmbeddingError(RuntimeError):
    pass


def _h(token: str) -> tuple[int, float]:
    d = hashlib.blake2b(token.encode(), digest_size=8).digest()
    v = int.from_bytes(d, "little")
    return v % DIM, 1.0 if (v >> 63) & 1 else -1.0


def hashing_embed(text: str) -> list[float]:
    words = [w for w in _WORD.findall(text.lower()) if w not in _STOP]
    vec = np.zeros(DIM, dtype=np.float32)
    feats: dict[str, float] = {}
    for w in words:
        feats["w:" + w] = feats.get("w:" + w, 0.0) + 1.0
        if len(w) > 4:
            for i in range(len(w) - 2):
                tri = "c:" + w[i : i + 3]
                feats[tri] = feats.get(tri, 0.0) + 0.25
    for a, b in zip(words, words[1:]):
        k = f"b:{a}_{b}"
        feats[k] = feats.get(k, 0.0) + 0.7
    for f, c in feats.items():
        idx, sign = _h(f)
        vec[idx] += sign * (1.0 + math.log(c)) if c >= 1 else sign * c
    n = float(np.linalg.norm(vec))
    if n > 0:
        vec /= n
    return vec.tolist()


def _fit_dim(v: list[float]) -> list[float]:
    if len(v) == DIM:
        return v
    if len(v) > DIM:  # Matryoshka-style truncation then renormalise
        arr = np.array(v[:DIM], dtype=np.float32)
    else:
        arr = np.pad(np.array(v, dtype=np.float32), (0, DIM - len(v)))
    n = float(np.linalg.norm(arr))
    return (arr / n if n else arr).tolist()


@lru_cache(maxsize=1)
def _st_model(name: str):
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError as e:  # pragma: no cover - optional dependency
        raise EmbeddingError("sentence-transformers is not installed (pip install sentence-transformers)") from e
    return SentenceTransformer(name)


def model_name() -> str:
    s = get_settings()
    return {
        "hashing": "hashing-lexical-v1",
        "sentence_transformers": s.sentence_transformer_model,
        "ollama": f"ollama:{s.ollama_embedding_model}",
        "gemini": f"gemini:{s.gemini_embedding_model}",
    }[s.embedding_provider]


def is_external() -> bool:
    return get_settings().embedding_provider == "gemini"


def embed_texts(texts: list[str], task: str = "document") -> list[list[float]]:
    s = get_settings()
    p = s.embedding_provider
    if not texts:
        return []
    if p == "hashing":
        return [hashing_embed(t) for t in texts]
    if p == "sentence_transformers":
        m = _st_model(s.sentence_transformer_model)
        return [_fit_dim(list(map(float, v))) for v in m.encode(texts, normalize_embeddings=True)]
    if p == "ollama":
        try:
            r = httpx.post(
                f"{s.ollama_base_url}/api/embed",
                json={"model": s.ollama_embedding_model, "input": texts},
                timeout=120,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise EmbeddingError(f"Ollama embedding failed: {e}") from e
        return [_fit_dim(v) for v in r.json()["embeddings"]]
    if p == "gemini":
        if not s.gemini_api_key:
            raise EmbeddingError("GEMINI_API_KEY is not configured")
        out: list[list[float]] = []
        task_type = "RETRIEVAL_QUERY" if task == "query" else "RETRIEVAL_DOCUMENT"
        for i in range(0, len(texts), 100):
            batch = texts[i : i + 100]
            body = {
                "requests": [
                    {
                        "model": f"models/{s.gemini_embedding_model}",
                        "content": {"parts": [{"text": t[:8000]}]},
                        "taskType": task_type,
                        "outputDimensionality": DIM,
                    }
                    for t in batch
                ]
            }
            try:
                r = httpx.post(
                    f"{s.gemini_base_url}/models/{s.gemini_embedding_model}:batchEmbedContents",
                    json=body,
                    headers={"x-goog-api-key": s.gemini_api_key},
                    timeout=120,
                )
            except httpx.HTTPError as e:
                raise EmbeddingError(f"Gemini embedding request failed: {e}") from e
            if r.status_code == 429:
                raise EmbeddingError("Gemini embedding quota exceeded; switch EMBEDDING_PROVIDER or retry later")
            if r.status_code >= 400:
                raise EmbeddingError(f"Gemini embedding failed ({r.status_code}): {r.text[:200]}")
            out.extend(_fit_dim(e["values"]) for e in r.json()["embeddings"])
        return out
    raise EmbeddingError(f"Unknown embedding provider {p}")


def embed_query(text: str) -> list[float]:
    return embed_texts([text], task="query")[0]
