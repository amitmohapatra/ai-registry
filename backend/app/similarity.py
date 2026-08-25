"""Hybrid semantic + lexical similarity with reciprocal-rank fusion.
Brute-force cosine via numpy — exact and sub-ms at registry scale (<=100k rows).
Swap for a pgvector VectorStore implementation without touching callers."""
import re
from typing import Dict, List, Optional

import numpy as np

_TOKEN = re.compile(r"[a-z0-9]+")


def embed_text_of(payload: dict) -> str:
    """What we embed: name + title + description + audience-overridden descriptions."""
    parts = [payload.get("name", ""), payload.get("title", ""), payload.get("description", "")]
    for ov in (payload.get("audiences") or {}).values():
        d = (ov.get("overrides") or {}).get("description")
        if d:
            parts.append(d)
    return " ".join(p for p in parts if p)


def _tokens(text: str) -> set:
    return set(_TOKEN.findall(text.lower()))


def rank(query_vec: List[float], query_text: str,
         candidates: List[dict], top_k: int = 10,
         exclude_id: Optional[str] = None) -> List[dict]:
    """candidates: [{id, product_id, product_key, type, name, text, vec}] -> ranked with scores.
    RRF fuses the cosine ranking with a lexical (Jaccard) ranking."""
    cands = [c for c in candidates if c["id"] != exclude_id and c.get("vec")]
    if not cands:
        return []
    mat = np.asarray([c["vec"] for c in cands], dtype=float)
    q = np.asarray(query_vec, dtype=float)
    cos = mat @ q  # vectors are pre-normalised
    qt = _tokens(query_text)
    lex = np.asarray([
        len(qt & _tokens(c["text"])) / (len(qt | _tokens(c["text"])) or 1) for c in cands
    ])
    K = 60.0
    rrf = np.zeros(len(cands))
    for scores in (cos, lex):
        order = np.argsort(-scores)
        ranks = np.empty_like(order)
        ranks[order] = np.arange(len(order))
        rrf += 1.0 / (K + ranks)
    top = np.argsort(-rrf)[:top_k]
    return [{
        "id": cands[i]["id"], "product_id": cands[i]["product_id"],
        "product_key": cands[i].get("product_key", ""), "type": cands[i]["type"],
        "name": cands[i]["name"],
        # cosine can go slightly negative with signed hashing; clamp — a negative
        # "percent match" is meaningless to a human
        "score": round(max(0.0, float(cos[i])), 4), "lexical": round(float(lex[i]), 4),
    } for i in top]


def duplicate_pairs(candidates: List[dict], threshold: float) -> List[dict]:
    """All pairs above cosine threshold — the duplicates/overlap report.
    One matrix multiply: O(n^2) values but vectorised; fine for registry scale."""
    cands = [c for c in candidates if c.get("vec")]
    if len(cands) < 2:
        return []
    mat = np.asarray([c["vec"] for c in cands], dtype=float)
    sim = mat @ mat.T
    threshold = max(0.05, threshold)          # <=0 would match the zeroed triangle itself
    iu, ju = np.triu_indices(len(cands), k=1)  # explicit upper-triangle indices: no
    keep = sim[iu, ju] >= threshold            # mirrored or self pairs, ever
    pairs = [{
        "a": {k: cands[i][k] for k in ("id", "name", "product_key", "type")},
        "b": {k: cands[j][k] for k in ("id", "name", "product_key", "type")},
        "score": round(max(0.0, float(sim[i, j])), 4),
        "cross_product": cands[i]["product_id"] != cands[j]["product_id"],
    } for i, j in zip(iu[keep], ju[keep])]
    return sorted(pairs, key=lambda p: -p["score"])


def explain_pair(a: dict, b: dict, a_vec=None, b_vec=None) -> dict:
    """Deterministic overlap breakdown between two entity payloads:
    which parts are common, per-field sub-scores, and actionable recommendations."""
    from .embeddings import HashingEmbedder
    emb = HashingEmbedder()

    def cos(x: str, y: str) -> float:
        vx, vy = emb._one(x), emb._one(y)
        return round(max(0.0, float(np.dot(vx, vy))), 4)

    def norm_tokens(text: str):
        return {emb._norm(t) for t in _TOKEN.findall(text.lower())} -                {emb._norm(t) for t in ("a", "an", "the", "by", "for", "of", "to", "in",
                                       "on", "with", "and", "or", "its", "is", "it")}

    a_desc, b_desc = a.get("description", ""), b.get("description", "")
    name_sim = cos(a.get("name", ""), b.get("name", ""))
    desc_sim = cos(a_desc, b_desc)
    a_params = set((a.get("input_schema") or {}).get("properties", {}))
    b_params = set((b.get("input_schema") or {}).get("properties", {}))
    shared_params = sorted(a_params & b_params)
    param_sim = round(len(a_params & b_params) / (len(a_params | b_params) or 1), 4)
    shared_terms = sorted(norm_tokens(a_desc) & norm_tokens(b_desc),
                          key=lambda t: -len(t))[:12]

    recs = []
    if desc_sim >= 0.8:
        recs.append({"field": "description", "severity": "high",
                     "message": "Descriptions are nearly identical. State explicitly what makes "
                                "each tool different (data source, scope, side effects)."})
    elif desc_sim >= 0.5 and shared_terms:
        recs.append({"field": "description", "severity": "medium",
                     "message": f"Descriptions share key terms ({', '.join(shared_terms[:5])}). "
                                "Add distinguishing context to whichever tool is narrower."})
    if name_sim >= 0.7:
        recs.append({"field": "name", "severity": "medium",
                     "message": "Names are very similar — a model may pick the wrong tool. "
                                "Prefix with domain or action specifics (e.g. billing_ / shipping_)."})
    if len(shared_params) >= 2:
        recs.append({"field": "parameters", "severity": "medium",
                     "message": f"Both expose parameters: {', '.join(shared_params)}. If they serve "
                                "the same need, consider one shared tool with audiences instead."})
    if not recs:
        recs.append({"field": "overall", "severity": "info",
                     "message": "Overlap is moderate; likely acceptable as separate tools."})
    return {"subscores": {"name": name_sim, "description": desc_sim, "parameters": param_sim},
            "shared": {"terms": shared_terms, "parameters": shared_params},
            "recommendations": recs}


# ---------------------------------------------------------------------------
# Reranker stage (Strategy): a cross-encoder reads BOTH texts together and
# scores the pair — materially more accurate than comparing two independent
# embeddings. Applied only to the top candidates, so cost stays bounded.
# ---------------------------------------------------------------------------
import math as _math
from abc import ABC as _ABC, abstractmethod as _abstractmethod


class Reranker(_ABC):
    name: str = "none"

    @_abstractmethod
    def score_pairs(self, query: str, candidates: List[str]) -> Optional[List[float]]:
        """Relevance per candidate in [0,1], or None if unavailable."""


class NoopReranker(Reranker):
    def score_pairs(self, query, candidates):
        return None


class FastEmbedReranker(Reranker):
    def __init__(self, model: str = "jinaai/jina-reranker-v1-turbo-en"):
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        self._model = TextCrossEncoder(model)
        self.name = f"fastembed:{model}"

    def score_pairs(self, query, candidates):
        if not candidates:
            return []
        logits = list(self._model.rerank(query, candidates))
        return [round(1.0 / (1.0 + _math.exp(-x)), 4) for x in logits]  # sigmoid -> [0,1]


_reranker: Optional[Reranker] = None


def reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        from .config import get_settings
        st = get_settings()
        if st.reranker == "fastembed":
            try:
                _reranker = FastEmbedReranker(st.reranker_model)
            except Exception:
                _reranker = NoopReranker()
        else:
            _reranker = NoopReranker()
    return _reranker


def apply_rerank(query_text: str, ranked: List[dict], text_of: Dict[str, str]) -> List[dict]:
    """Rescore ranked matches with the cross-encoder; falls back to cosine scores.
    Adds method: 'reranked' | 'cosine' so consumers know what the % means."""
    rr = reranker()
    texts = [text_of.get(m["id"], "") for m in ranked]
    scores = rr.score_pairs(query_text, texts) if texts else []
    if not scores:
        for m in ranked:
            m["method"] = "cosine"
        return ranked
    for m, s in zip(ranked, scores):
        m["cosine"] = m["score"]
        m["score"] = s
        m["method"] = "reranked"
    return sorted(ranked, key=lambda m: -m["score"])


def rerank_pairs(pairs: List[dict], text_of: Dict[str, str], cap: int = 100) -> List[dict]:
    """Duplicates report: rescore the top pairs pairwise. Pairs beyond `cap`
    keep their cosine score (logged via method field)."""
    rr = reranker()
    if isinstance(rr, NoopReranker):
        for p in pairs:
            p["method"] = "cosine"
        return pairs
    for p in pairs[:cap]:
        s = rr.score_pairs(text_of.get(p["a"]["id"], ""), [text_of.get(p["b"]["id"], "")])
        if s:
            p["cosine"] = p["score"]
            p["score"] = s[0]
            p["method"] = "reranked"
    for p in pairs[cap:]:
        p["method"] = "cosine"
    return sorted(pairs, key=lambda p: -p["score"])
