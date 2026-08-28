"""Deterministic naming/description suggestions for flagged lookalike tools.

One home for this logic (used by the similar-preview endpoint): suggested names
are guaranteed valid and collision-free across EVERY product, so 'use this
instead' is always safe to click."""
import re
from typing import List, Optional, Set

from .similarity import NoopReranker, blend_breakdown, desc_text_of, name_similarity, reranker

_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "by", "for", "of", "to", "in", "on", "with", "and", "or",
         "its", "is", "it", "this", "that", "get", "set", "from", "into", "onto",
         "via", "using", "about", "over", "under", "each", "all", "any", "when",
         "then", "than", "also", "only", "will", "your", "their"}


def humanize(name: str) -> str:
    """get_payment_status -> 'Get payment status'."""
    words = name.replace("-", "_").split("_")
    return " ".join(words).strip().capitalize()


def _distinct_tokens(draft: dict, other: dict) -> List[str]:
    """Informative words that make the draft different from the lookalike —
    drawn from its description and parameter names."""
    other_text = (desc_text_of(other) + " " + other.get("name", "").replace("_", " ")).lower()
    other_tokens = set(_TOKEN.findall(other_text))
    mine: List[str] = []
    own_params = list((draft.get("input_schema") or {}).get("properties", {}))
    for source in (desc_text_of(draft), " ".join(own_params).replace("_", " ")):
        for t in _TOKEN.findall(source.lower()):
            if len(t) > 3 and t not in _STOP and t not in other_tokens and t not in mine:
                mine.append(t)
    return mine


def suggest_names(draft: dict, other: dict, product_key: str,
                  taken: Set[str]) -> List[str]:
    """Up to 3 alternative names: qualified by what's genuinely different, or
    domain-prefixed. Every suggestion is unique across the whole registry."""
    current = draft.get("name", "")
    taken_l = {t.lower() for t in taken}
    out: List[str] = []

    def consider(candidate: str):
        c = candidate.strip("_")
        if (c and c.lower() != current.lower() and c.lower() not in taken_l
                and _NAME_RE.match(c) and c not in out):
            out.append(c)

    current_tokens = set(current.lower().replace("-", "_").split("_"))
    for tok in _distinct_tokens(draft, other):
        if tok in current_tokens:
            continue                              # never fetch_invoice_retrieve_retrieve
        consider(f"{current}_{tok}")
        if len(out) >= 3:
            break
    consider(f"{product_key.replace('-', '_')}_{current}")
    base = current.split("_")
    if len(base) > 1:                       # reorder: object-first variant
        consider("_".join(base[1:] + base[:1]))
    return out[:3]


def description_tip(draft: dict, other: dict) -> Optional[str]:
    other_name = other.get("name", "")
    distinct = _distinct_tokens(draft, other)
    if distinct:
        return (f"What sets this tool apart: {', '.join(distinct[:4])}. Lead with that — "
                f"start the description with it — so a model can't read this as the same "
                f"action as {other_name}.")
    return (f"The description doesn't say anything that {other_name} doesn't already "
            f"say. Add what makes this tool different — its data source, its scope, "
            f"or when someone should pick it over {other_name}.")


def build_suggestions(draft: dict, other: dict, product_key: str,
                      taken: Set[str], name_collision: bool) -> dict:
    """Per-section recommendations, honestly scored: each rename shows the
    predicted OVERALL match (recomputed with the real scorer), and the
    description gets an applyable differentiator sentence — the field that
    actually moves the number."""
    rr = reranker()
    neural = not isinstance(rr, NoopReranker)
    other_name = other.get("name", "")
    current_sim = name_similarity(draft.get("name", ""), other_name)
    current_overall = blend_breakdown(rr, draft, other)["overall"] if neural else None
    names = []
    if name_collision:
        for n in suggest_names(draft, other, product_key, taken):
            if name_similarity(n, other_name) >= current_sim - 0.1:
                continue
            renamed = {**draft, "name": n, "title": humanize(n)}
            new_overall = blend_breakdown(rr, renamed, other)["overall"]
            if neural and new_overall > current_overall - 0.02:
                continue                    # a rename that doesn't help is noise
            names.append({"name": n, "title": humanize(n),
                          "new_overall": round(new_overall, 2)})
    title_suggestion = None
    if names:
        title_suggestion = names[0]["title"]
    elif (d := _distinct_tokens(draft, other)):
        title_suggestion = f"{humanize(draft.get('name', ''))} ({d[0]})"
    distinct = _distinct_tokens(draft, other)
    description_suggestion = None
    if distinct:
        description_suggestion = (f"{draft.get('description', '').rstrip('. ')}. "
                                  f"Unlike {other_name}, this is specifically about "
                                  f"{', '.join(distinct[:3])}.")
    return {
        "names": names,
        "current_name_match": round(current_sim, 2),
        "title_suggestion": title_suggestion,
        "description_suggestion": description_suggestion,
        "description_tip": description_tip(draft, other),
    }
