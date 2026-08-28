"""Embedding providers (Strategy). Registry works with zero external services:
- hashing: deterministic local feature-hashing embedder (default, no deps, no keys)
- fastembed: local ONNX MiniLM-class model if the library is installed
- openai: any OpenAI-compatible embeddings API if the customer supplies a key
Vectors are L2-normalised so cosine similarity is a dot product."""
import hashlib
import math
import re
from abc import ABC, abstractmethod
from typing import List

_TOKEN = re.compile(r"[a-z0-9]+")
DIM = 512
_STOP = {"a", "an", "the", "by", "for", "of", "to", "in", "on", "with", "and", "or",
         "its", "is", "it", "this", "that", "be", "as", "at", "from"}


class EmbeddingProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def embed(self, texts: List[str]) -> List[List[float]]: ...


class HashingEmbedder(EmbeddingProvider):
    """v2 feature hashing: stopword-filtered word uni/bi-grams PLUS character 3/4-grams
    per token. Char-grams make paraphrases and morphological variants (fetch/fetching,
    invoice/invoices) share features, which materially improves duplicate detection
    while staying deterministic, O(chars), and dependency-free. Sublinear tf weighting
    so a repeated word cannot dominate the vector."""

    name = "hashing-512-v3"

    async def embed(self, texts: List[str]) -> List[List[float]]:
        return [self._one(t) for t in texts]

    @staticmethod
    def _norm(t: str) -> str:
        # light suffix stripping so plurals/inflections share features
        for suf in ("ing", "ed", "es", "s"):
            if len(t) > len(suf) + 3 and t.endswith(suf):
                return t[: -len(suf)]
        return t

    @classmethod
    def _features(cls, text: str):
        tokens = [cls._norm(t) for t in _TOKEN.findall(text.lower()) if t not in _STOP]
        feats = {}
        def add(f, w):
            feats[f] = feats.get(f, 0.0) + w
        for t in tokens:
            add(f"w:{t}", 1.0)
            for n in (3, 4):
                for i in range(len(t) - n + 1):
                    add(f"c{n}:{t[i:i+n]}", 0.35)
        for a, b in zip(tokens, tokens[1:], strict=False):
            add(f"b:{a}_{b}", 0.8)
        return feats

    def _one(self, text: str) -> List[float]:
        vec = [0.0] * DIM
        for feat, weight in self._features(text).items():
            w = 1.0 + math.log(weight) if weight > 1.0 else weight   # sublinear tf
            h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "big")
            vec[h % DIM] += -w if (h >> 63) & 1 else w
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [round(v / norm, 6) for v in vec]


class FastEmbedProvider(EmbeddingProvider):
    name = "fastembed-minilm"

    def __init__(self, model: str = "BAAI/bge-base-en-v1.5"):
        import os
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model, threads=max(2, (os.cpu_count() or 4) // 2))
        self.name = f"fastembed:{model}"

    async def embed(self, texts):
        import numpy as np
        out = []
        for v in self._model.embed(texts):
            a = np.asarray(v, dtype=float)
            n = float(np.linalg.norm(a)) or 1.0
            out.append([round(float(x) / n, 6) for x in a])
        return out


class OpenAICompatProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-small",
                 base_url: str = "https://api.openai.com/v1"):
        self._key, self._model, self._base = api_key, model, base_url
        self.name = f"api:{model}"

    async def embed(self, texts):
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{self._base}/embeddings",
                                  headers={"Authorization": f"Bearer {self._key}"},
                                  json={"model": self._model, "input": texts})
            r.raise_for_status()
            return [d["embedding"] for d in r.json()["data"]]


def build_provider(kind: str = "hashing", api_key: str = "", model: str = "") -> EmbeddingProvider:
    if kind == "openai" and api_key:
        return OpenAICompatProvider(api_key, model or "text-embedding-3-small")
    if kind == "fastembed":
        try:
            return FastEmbedProvider(model or "BAAI/bge-base-en-v1.5")
        except Exception:
            pass                       # no library / no network: hashing fallback below
    return HashingEmbedder()
