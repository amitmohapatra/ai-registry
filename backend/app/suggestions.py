"""Deterministic naming/description suggestions for flagged lookalike tools.

One home for this logic (used by the similar-preview endpoint): suggested names
are guaranteed valid and collision-free across EVERY product, so 'use this
instead' is always safe to click."""
import re
from typing import List, Optional, Set

from .similarity import desc_text_of, name_similarity

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

    for tok in _distinct_tokens(draft, other)[:3]:
        consider(f"{current}_{tok}")
    consider(f"{product_key.replace('-', '_')}_{current}")
    base = current.split("_")
    if len(base) > 1:                       # reorder: object-first variant
        consider("_".join(base[1:] + base[:1]))
    return out[:3]


def description_tip(draft: dict, other: dict) -> Optional[str]:
    other_name = other.get("name", "")
    distinct = _distinct_tokens(draft, other)
    if distinct:
        return (f"Make the description say what only this tool does — for example "
                f"mention: {', '.join(distinct[:4])}. Right now it reads almost the "
                f"same as {other_name}.")
    return (f"The description doesn't say anything that {other_name} doesn't already "
            f"say. Add what makes this tool different — its data source, its scope, "
            f"or when someone should pick it over {other_name}.")


def build_suggestions(draft: dict, other: dict, product_key: str,
                      taken: Set[str], name_collision: bool) -> dict:
    """Per-section recommendations. Every suggested name is VALIDATED before
    being offered: unique across all products, and its predicted match against
    the conflicting tool must be a real improvement (reported as new_match)."""
    other_name = other.get("name", "")
    current_sim = name_similarity(draft.get("name", ""), other_name)
    names = []
    if name_collision:
        for n in suggest_names(draft, other, product_key, taken):
            predicted = name_similarity(n, other_name)
            if predicted < current_sim - 0.1:          # only offer genuine improvements
                names.append({"name": n, "title": humanize(n),
                              "new_match": round(predicted, 2)})
    return {
        "names": names,
        "title_tip": (f"Give it a title that says what makes it different — e.g. "
                      f"'{humanize(names[0]['name'])}'" if names else None),
        "description_tip": description_tip(draft, other),
    }
