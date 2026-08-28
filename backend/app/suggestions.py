"""Deterministic naming/description suggestions for flagged lookalike tools.

One home for this logic (used by the similar-preview endpoint): suggested names
are guaranteed valid and collision-free across EVERY product, so 'use this
instead' is always safe to click."""
import re
from typing import List, Optional, Set

from .similarity import (NoopReranker, blend_breakdown, desc_text_of, is_action_word,
                         name_similarity, reranker)

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
            if (len(t) > 3 and t not in _STOP and t not in other_tokens
                    and t not in mine and not is_action_word(t)):
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


def _rewrite_candidates(draft: dict, other: dict, product_key: str) -> List[str]:
    """Affirmative rewrites (research: models barely register negation — 'Unlike X'
    keeps the similarity; a rewrite must CHANGE what the text asserts). Lead with
    the distinct terms, drop the shared phrasing, stay positive."""
    d = _distinct_tokens(draft, other)
    params = list((draft.get("input_schema") or {}).get("properties", {}))
    main_param = (params[0].replace("_", " ") if params else "identifier")
    out = []
    if len(d) >= 2:
        anchor = f"{d[0]} {d[1]}"
    elif d:
        anchor = d[0]
    else:
        return []
    out.append(f"{anchor.capitalize()} lookup for {product_key}: returns the "
               f"{anchor} record matching a given {main_param}.")
    out.append(f"Look up the {anchor} held in {product_key} for a specific "
               f"{main_param}; no other record types are returned.")
    out.append(f"Given a {main_param}, returns {product_key}'s {anchor} record "
               f"for it — scoped to {product_key} data only.")
    return out


def build_suggestions(draft: dict, other: dict, product_key: str,
                      taken: Set[str], name_collision: bool,
                      threshold: float = 0.5) -> dict:
    """2-3 pickable options per field (name / title / description), each with the
    PREDICTED overall match after applying just that option — computed with the
    real scorer, offered only when it genuinely improves things."""
    rr = reranker()
    neural = not isinstance(rr, NoopReranker)
    other_name = other.get("name", "")
    current_sim = name_similarity(draft.get("name", ""), other_name)
    current_overall = blend_breakdown(rr, draft, other)["overall"] if neural else None

    def predicted(patch: dict) -> Optional[float]:
        if not neural:
            return None
        return round(blend_breakdown(rr, {**draft, **patch}, other)["overall"], 2)

    def improving(options):
        # Never offer an option that makes the match WORSE; within that, rank by
        # predicted overall (best first). The visible "-> X%" on each chip is the
        # honesty mechanism — a rename that only gets to 78% shows exactly that.
        if not neural:
            return options[:3]
        kept = [o for o in options if o["new_overall"] is not None
                and o["new_overall"] <= round(current_overall, 2)]
        return sorted(kept, key=lambda o: o["new_overall"])[:3]

    names = []
    if name_collision:
        for n in suggest_names(draft, other, product_key, taken):
            if name_similarity(n, other_name) > current_sim:
                continue                          # must not be MORE name-alike
            names.append({"name": n, "title": humanize(n),
                          "new_overall": predicted({"name": n, "title": humanize(n)})})
    names = improving(names)

    d = _distinct_tokens(draft, other)
    title_texts = [humanize(n["name"]) for n in names]
    if d:
        title_texts.append(f"{humanize(draft.get('name', ''))} ({d[0]})")
        if len(d) > 1:
            title_texts.append(f"{d[0].capitalize()} {d[1]} — {humanize(draft.get('name', ''))}")
    titles = improving([{"title": t, "new_overall": predicted({"title": t})}
                        for t in dict.fromkeys(title_texts) if t != draft.get("title")])

    descriptions = improving([{"text": t, "new_overall": predicted({"description": t})}
                              for t in _rewrite_candidates(draft, other, product_key)])

    resolution_hint = None
    if neural and current_overall is not None and current_overall >= threshold:
        best = min([o["new_overall"] for o in names + titles]
                   + [o["new_overall"] for o in descriptions] + [1.0])
        if best >= threshold:
            bd = blend_breakdown(rr, draft, other)
            top_field = max(bd["contributions"], key=lambda f: bd["contributions"][f])
            resolution_hint = (
                f"None of the suggestions get this below {round(threshold*100)}% — the "
                f"remaining overlap is mostly in the {top_field}. If these really are "
                f"different tools, change the {top_field} to say something {other_name} "
                f"doesn't. If they do the same thing, reuse {other_name} instead of "
                f"creating a near-duplicate.")

    return {
        "names": names,
        "titles": titles,
        "descriptions": descriptions,
        "current_name_match": round(current_sim, 2),
        "description_tip": None if descriptions else description_tip(draft, other),
        "resolution_hint": resolution_hint,
    }
