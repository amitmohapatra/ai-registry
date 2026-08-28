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


def desc_text_of(payload: dict) -> str:
    """Description semantics only — names/titles deliberately excluded, so the
    displayed match % measures MEANING, not naming coincidences."""
    parts = [payload.get("description", "")]
    for ov in (payload.get("audiences") or {}).values():
        d = (ov.get("overrides") or {}).get("description")
        if d:
            parts.append(d)
    return " ".join(p for p in parts if p)


def name_similarity(a: str, b: str) -> float:
    """Cheap deterministic name-collision signal (char-gram cosine)."""
    from .embeddings import HashingEmbedder
    emb = HashingEmbedder()
    va, vb = emb._one(a.replace("_", " ")), emb._one(b.replace("_", " "))
    return round(max(0.0, float(np.dot(va, vb))), 4)


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
    } for i, j in zip(iu[keep], ju[keep], strict=True)]
    return sorted(pairs, key=lambda p: -p["score"])


def _sym_rerank(rr, a: str, b: str) -> Optional[float]:
    """Symmetric cross-encoder score without guards — for sub-scores."""
    if not a.strip() or not b.strip():
        return 0.0
    fwd = rr.score_pairs(a, [b])
    bwd = rr.score_pairs(b, [a])
    if not fwd or not bwd:
        return None
    return round((fwd[0] + bwd[0]) / 2, 4)


def explain_pair(a: dict, b: dict, a_vec=None, b_vec=None) -> dict:
    """Overlap breakdown between two entity payloads: per-field sub-scores computed
    by the SAME engine as the headline match % (neural when available), plus shared
    evidence and actionable recommendations."""
    from .embeddings import HashingEmbedder
    emb = HashingEmbedder()
    rr = reranker()
    neural = not isinstance(rr, NoopReranker)

    def cos(x: str, y: str) -> float:
        if neural:
            s = _sym_rerank(rr, x, y)
            if s is not None:
                return s
        vx, vy = emb._one(x), emb._one(y)
        return round(max(0.0, float(np.dot(vx, vy))), 4)

    def norm_tokens(text: str):
        return {emb._norm(t) for t in _TOKEN.findall(text.lower())} -                {emb._norm(t) for t in ("a", "an", "the", "by", "for", "of", "to", "in",
                                       "on", "with", "and", "or", "its", "is", "it")}

    a_desc, b_desc = a.get("description", ""), b.get("description", "")
    title_sim = None
    bd = None
    if neural:
        bd = blend_breakdown(rr, a, b)
        fs = bd["subscores"]
        name_sim, desc_sim = fs["name"], fs["description"]
        title_sim = fs.get("title")
    else:
        name_sim = cos(a.get("name", "").replace("_", " "), b.get("name", "").replace("_", " "))
        desc_sim = cos(a_desc, b_desc)
    a_params = set((a.get("input_schema") or {}).get("properties", {}))
    b_params = set((b.get("input_schema") or {}).get("properties", {}))
    shared_params = sorted(a_params & b_params)
    param_sim = param_similarity(a, b) or 0.0
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
    if desc_sim >= 0.5 and (declared_effect(a) is None or declared_effect(b) is None):
        recs.append({"field": "annotations", "severity": "info",
                     "message": "Tip: tick the 'Read-only' or 'Destructive' checkbox on both "
                                "tools. That tells us what each tool actually does to data, "
                                "so we can say for sure whether they really overlap."})
    if not recs:
        recs.append({"field": "overall", "severity": "info",
                     "message": "Overlap is moderate; likely acceptable as separate tools."})
    if bd is not None:
        subs, overall, contributions = bd["subscores"], bd["overall"], bd["contributions"]
        capped_by = bd["capped_by"]
    else:
        subs = {"name": name_sim, "description": desc_sim, "parameters": param_sim}
        if title_sim is not None:
            subs["title"] = title_sim
        weights = {f: w for f, w in FIELD_WEIGHTS.items() if f in subs}
        total_w = sum(weights.values())
        contributions = {f: round(subs[f] * w / total_w, 4) for f, w in weights.items()}
        overall = round(sum(contributions.values()), 4)
        capped_by = None
    return {"subscores": subs,
            "overall": overall,
            "contributions": contributions,
            "capped_by": capped_by,
            "params": {"a": sorted(a_params), "b": sorted(b_params)},
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
    def __init__(self, model: str = "BAAI/bge-reranker-base"):
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


_INFORMATIVE = re.compile(r"[a-z]{3,}")


def _informative_count(text: str) -> int:
    from .embeddings import _STOP
    return len([t for t in _INFORMATIVE.findall(text.lower()) if t not in _STOP])


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / (len(ta | tb) or 1)


_ACTION_CLASSES = {
    "read":   {"get", "fetch", "retrieve", "list", "search", "find", "query", "view",
               "show", "read", "lookup", "look", "download", "check", "preview",
               "recommend", "suggest", "estimate", "calculate", "compute", "inspect",
               "describe", "count"},
    "create": {"create", "add", "new", "register", "generate", "make", "submit",
               "insert", "upload", "issue", "open"},
    "update": {"update", "modify", "edit", "set", "change", "adjust", "patch",
               "rename", "enable", "disable", "apply", "assign"},
    "delete": {"delete", "remove", "erase", "cancel", "purge", "void", "revoke",
               "archive", "close"},
    "send":   {"send", "notify", "email", "push", "dispatch", "publish", "forward"},
    "refund": {"refund", "reimburse", "chargeback"},
    "charge": {"capture", "charge", "authorize", "debit", "bill", "invoice_charge"},
}
def _INFLECT(w: str) -> set:
    return {w, w + "s", w + "es", w + "d", w + "ed", w + "ing"}


def action_class(text: str) -> Optional[str]:
    """First recognizable action verb's class — for tools, the verb IS the capability."""
    tokens = _TOKEN.findall(text.lower())
    for t in tokens:
        for cls, words in _ACTION_CLASSES.items():
            if any(t in _INFLECT(w) for w in words):
                return cls
    return None


def declared_effect(payload: dict) -> Optional[str]:
    """Effect class from EXPLICIT MCP annotations — declared semantics, correct by
    construction (the OWL-S effect-matching / HTTP safe-method model). Absent
    annotations return None: absence is never treated as false."""
    ann = payload.get("annotations") or {}
    if "readOnlyHint" not in ann:
        return None
    if ann["readOnlyHint"] is True:
        return "read"
    if ann.get("destructiveHint") is True:
        return "destructive"
    if "destructiveHint" in ann:
        return "write"          # explicitly declared non-destructive write
    return "write?"             # a write; destructiveness undeclared


def capability_cap(a: dict, b: dict) -> Optional[float]:
    """Different capabilities cannot be duplicates, whatever the topic overlap.
    Precedence: declared annotations (tier 1) -> verb-class inference from the
    descriptions (tier 2 fallback) -> None (reranker score stands)."""
    ea, eb = declared_effect(a), declared_effect(b)
    if ea and eb:
        if ("read" in (ea, eb)) and ea != eb:
            return 0.45                      # read vs any write: distinct, hard cap
        if {ea, eb} == {"destructive", "write"}:
            return 0.50                      # both write, destructiveness differs: at-threshold cap
        return None                          # same declared class: no cap
    ca = inferred_class(a)
    cb = inferred_class(b)
    if ca and cb and ca != cb:
        return 0.45
    return None


def inferred_class(payload: dict) -> Optional[str]:
    """Verb class from the description, falling back to the tool NAME
    (get_payment_status -> 'get' -> read) when the description's verbs are
    outside the lexicon."""
    return (action_class(desc_text_of(payload))
            or action_class(payload.get("name", "").replace("_", " ")))


# Field weights for the overall match %. The overall is a TRANSPARENT weighted
# average of the per-field scores the UI shows — absent fields (no titles, no
# params on either side) drop out and the remaining weights renormalise.
FIELD_WEIGHTS = {"description": 0.55, "name": 0.15, "title": 0.10, "parameters": 0.20}


def param_similarity(a: dict, b: dict) -> Optional[float]:
    """Structural overlap of parameter names; None when neither tool has params
    (an absent field shouldn't drag the average down)."""
    pa = set((a.get("input_schema") or {}).get("properties", {}))
    pb = set((b.get("input_schema") or {}).get("properties", {}))
    if not pa and not pb:
        return None
    return round(len(pa & pb) / (len(pa | pb) or 1), 4)


def field_scores(rr: "Reranker", a: dict, b: dict) -> dict:
    """Per-field semantic scores — the SAME numbers the UI breakdown shows."""
    scores = {"description": equivalence_score(rr, desc_text_of(a), desc_text_of(b)) or 0.0,
              "name": _sym_rerank(rr, a.get("name", "").replace("_", " "),
                                  b.get("name", "").replace("_", " ")) or 0.0}
    ta, tb = a.get("title", ""), b.get("title", "")
    if ta and tb and ta != a.get("name") and tb != b.get("name"):
        scores["title"] = _sym_rerank(rr, ta, tb) or 0.0
    p = param_similarity(a, b)
    if p is not None:
        scores["parameters"] = p
    return scores


def blend_breakdown(rr: "Reranker", a_payload: dict, b_payload: dict) -> dict:
    """THE similarity computation — one function, used by every surface.

    overall = weighted average of field scores, with guards (thin-description
    cap; capability cap from annotations/verbs). When a guard caps the score,
    contributions are scaled proportionally so they STILL sum to the shown
    overall — the breakdown always adds up, everywhere, by construction."""
    a_desc, b_desc = desc_text_of(a_payload), desc_text_of(b_payload)
    scores = field_scores(rr, a_payload, b_payload)
    weights = {f: w for f, w in FIELD_WEIGHTS.items() if f in scores}
    total_w = sum(weights.values())
    raw = sum(scores[f] * w for f, w in weights.items()) / total_w
    overall = raw
    capped_by = None
    if (_informative_count(a_desc) < 2 or _informative_count(b_desc) < 2) and overall > 0.35:
        overall, capped_by = 0.35, "thin-description"
    cap = capability_cap(a_payload, b_payload)
    if cap is not None and overall > cap:
        overall, capped_by = cap, "different-capability"
    factor = (overall / raw) if raw > 0 else 0.0
    contributions = {f: round(scores[f] * w / total_w * factor, 4)
                     for f, w in weights.items()}
    return {"overall": round(overall, 4),
            "subscores": {f: round(v, 4) for f, v in scores.items()},
            "contributions": contributions,
            "capped_by": capped_by}


def tool_equivalence(rr: "Reranker", a_payload: dict, b_payload: dict) -> Optional[float]:
    return blend_breakdown(rr, a_payload, b_payload)["overall"]


def equivalence_score(rr: "Reranker", a: str, b: str) -> Optional[float]:
    """Symmetric semantic-equivalence between two descriptions.

    - thin guard: a description under 2 informative words cannot claim high
      similarity — capped at 0.35 (the honest answer is 'not enough text to say')
    - symmetric rerank: cross-encoders are query->document relevance models;
      averaging both directions turns relevance into equivalence
    - near-identical floor: >=80% token overlap IS equivalence, whatever the model says
    """
    if _informative_count(a) < 2 or _informative_count(b) < 2:
        fwd = rr.score_pairs(a, [b])
        return min(0.35, fwd[0]) if fwd else None
    fwd = rr.score_pairs(a, [b])
    bwd = rr.score_pairs(b, [a])
    if not fwd or not bwd:
        return None
    score = (fwd[0] + bwd[0]) / 2
    if _jaccard(a, b) >= 0.8:
        score = max(score, 0.9)
    # different action classes = different capabilities, whatever the topic overlap:
    # "create an invoice" and "fetch an invoice" are never duplicates
    ca, cb = action_class(a), action_class(b)
    if ca and cb and ca != cb:
        score = min(score, 0.45)
    return round(score, 4)


def apply_rerank(query_payload: dict, ranked: List[dict],
                 payload_of: Dict[str, dict]) -> List[dict]:
    """Rescore ranked matches with whole-record symmetric equivalence; falls back
    to cosine. method: 'reranked' | 'thin-description' | 'cosine'."""
    rr = reranker()
    if isinstance(rr, NoopReranker):
        for m in ranked:
            m["method"] = "cosine"
        return ranked
    thin_query = _informative_count(desc_text_of(query_payload)) < 2
    for m in ranked:
        other = payload_of.get(m["id"]) or {}
        bd = blend_breakdown(rr, query_payload, other)
        m["cosine"] = m["score"]
        m["score"] = bd["overall"]
        m["breakdown"] = bd
        m["method"] = "thin-description" if (thin_query or
            _informative_count(desc_text_of(other)) < 2) else "reranked"
    return sorted(ranked, key=lambda m: -m["score"])


def rerank_pairs(pairs: List[dict], payload_of: Dict[str, dict], cap: int = 100) -> List[dict]:
    """Duplicates report: rescore the top pairs pairwise. Pairs beyond `cap`
    keep their cosine score (logged via method field)."""
    rr = reranker()
    if isinstance(rr, NoopReranker):
        for p in pairs:
            p["method"] = "cosine"
        return pairs
    for p in pairs[:cap]:
        bd = blend_breakdown(rr, payload_of.get(p["a"]["id"]) or {},
                             payload_of.get(p["b"]["id"]) or {})
        p["cosine"] = p["score"]
        p["score"] = bd["overall"]
        p["breakdown"] = bd
        p["method"] = "reranked"
    for p in pairs[cap:]:
        p["method"] = "cosine"
    return sorted(pairs, key=lambda p: -p["score"])
