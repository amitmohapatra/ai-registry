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
        out.append(f"{d[0].capitalize()} {d[1]} lookup for {product_key}: returns the "
                   f"{d[0]} {d[1]} record matching a given {main_param}.")
        out.append(f"Look up the {d[0]} {d[1]} held in {product_key} for a specific "
                   f"{main_param}; no other record types are returned.")
    elif d:
        out.append(f"{d[0].capitalize()} lookup for {product_key}: returns the {d[0]} "
                   f"record matching a given {main_param}.")
    return out


def build_fix_bundle(draft: dict, others: List[dict], product_key: str,
                     taken: Set[str], threshold: float) -> Optional[dict]:
    """Generate-and-TEST: propose name+title+description combinations and only
    return one whose blended match is verified below the threshold against
    EVERY provided tool (all products). No unverified promises."""
    rr = reranker()
    if isinstance(rr, NoopReranker) or not others:
        return None
    top = others[0]
    name_opts = [draft.get("name", "")] + suggest_names(draft, top, product_key, taken)[:1]
    desc_opts = _rewrite_candidates(draft, top, product_key)
    best = None
    for desc in desc_opts:
        for name in name_opts:
            candidate = {**draft, "name": name, "title": humanize(name),
                         "description": desc}
            worst = max(blend_breakdown(rr, candidate, o)["overall"] for o in others)
            if worst < threshold - 0.02 and (best is None or worst < best["validated_max"]):
                best = {"name": name, "title": humanize(name), "description": desc,
                        "validated_max": round(worst, 2), "checked_against": len(others)}
        if best and best["name"] == draft.get("name", ""):
            break                                    # minimal change wins
    return best


def build_suggestions(draft: dict, other: dict, product_key: str,
                      taken: Set[str], name_collision: bool,
                      threshold: float = 0.5) -> dict:
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
    desc_now = draft.get("description", "")
    already_differentiated = f"Unlike {other_name}" in desc_now
    if distinct and not already_differentiated:          # idempotent: never re-append
        text = (f"{desc_now.rstrip('. ')}. Unlike {other_name}, this is specifically "
                f"about {', '.join(distinct[:3])}.")
        entry = {"text": text}
        if neural:
            pred = blend_breakdown(rr, {**draft, "description": text}, other)["overall"]
            entry["new_overall"] = round(pred, 2)
            if pred >= threshold:                         # would NOT resolve -> don't offer
                entry = None
        description_suggestion = entry

    # nothing we can offer gets below the threshold -> say what WOULD resolve it
    resolution_hint = None
    if neural and current_overall is not None and current_overall >= threshold:
        best_offer = min([n["new_overall"] for n in names]
                         + ([description_suggestion["new_overall"]]
                            if description_suggestion and "new_overall" in description_suggestion
                            else []) + [1.0])
        if best_offer >= threshold:
            bd = blend_breakdown(rr, draft, other)
            top_field = max(bd["contributions"], key=lambda f: bd["contributions"][f])
            resolution_hint = (
                f"None of the quick fixes get this below {round(threshold*100)}% — the "
                f"remaining overlap is mostly in the {top_field}. If these really are "
                f"different tools, change the {top_field} to say something {other_name} "
                f"doesn't. If they do the same thing, reuse {other_name} instead of "
                f"creating a near-duplicate.")
    return {
        "names": names,
        "current_name_match": round(current_sim, 2),
        "title_suggestion": title_suggestion,
        "description_suggestion": description_suggestion,
        "description_tip": description_tip(draft, other),
        "resolution_hint": resolution_hint,
    }
