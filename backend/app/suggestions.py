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
         "then", "than", "also", "only", "will", "your", "their",
         # generic filler that never distinguishes one tool from another
         "unique", "returns", "returned", "record", "records", "result", "results",
         "single", "complete", "current", "specific", "details", "every", "whenever",
         "example", "answer", "question", "structured", "json", "include", "includes",
         "including", "download", "link", "generated", "must", "already", "exist",
         "exists", "need", "full", "tool", "tools", "data", "information", "given",
         "which", "should", "would", "could", "them", "these", "those", "have", "has",
         "been", "were", "more", "most", "other", "another", "such", "like", "well",
         "just", "first", "right", "there", "here", "what", "whether", "before",
         "after", "while", "during", "does", "unknown", "linked", "applied"}


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
    counts: dict = {}
    own_params = list((draft.get("input_schema") or {}).get("properties", {}))
    for source in (desc_text_of(draft), " ".join(own_params).replace("_", " ")):
        for t in _TOKEN.findall(source.lower()):
            if (len(t) > 3 and t not in _STOP and t not in other_tokens
                    and not is_action_word(t)):
                if t not in counts:
                    mine.append(t)
                counts[t] = counts.get(t, 0) + 1
    # salience first: a word the description keeps coming back to ("customer",
    # "refund") beats whatever generic word happened to appear earliest
    return sorted(mine, key=lambda t: (-counts[t], mine.index(t)))


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


def _an(word: str) -> str:
    return f"an {word}" if word[:1].lower() in "aeiou" else f"a {word}"


def _dedupe_words(text: str) -> str:
    """Collapse immediately repeated words ('customer customer record')."""
    out: List[str] = []
    for w in text.split(" "):
        if not out or w.lower() != out[-1].lower():
            out.append(w)
    return " ".join(out)


def _rewrite_candidates(draft: dict, other: dict, product_key: str) -> List[str]:
    """Affirmative rewrites (research: models barely register negation — 'Unlike X'
    keeps the similarity; a rewrite must CHANGE what the text asserts). We generate
    a POOL of structurally different skeletons — same-template rewrites read as
    near-duplicates of each other — and the caller keeps only the candidates whose
    worst-case score against every nearby tool clears the threshold. Long
    descriptions the author invested in are edited (lead reworked, scope added)
    rather than replaced."""
    d = _distinct_tokens(draft, other)
    params = list((draft.get("input_schema") or {}).get("properties", {}))
    main_param = (params[0].replace("_", " ") if params else "identifier")
    if not d:
        return []
    a1 = f"{d[0]} {d[1]}" if len(d) >= 2 else d[0]
    a2 = (f"{d[2]} {d[3]}" if len(d) >= 4 else (d[2] if len(d) >= 3 else a1))
    pk = product_key
    skeletons = [
        f"{a1.capitalize()} lookup for {pk}: returns the {a1} record matching {_an(main_param)}.",
        f"Read-only access to {pk}'s {a1} data, keyed by {main_param}.",
        f"{pk.capitalize()}-side query that resolves {_an(main_param)} to its {a2} entry.",
        f"Reports the {a2} held by {pk} for one {main_param}; nothing else is returned.",
        f"Look up the {a1} held in {pk} for a specific {main_param}; no other record types are returned.",
    ]
    full = desc_text_of(draft).strip()
    out = []
    if len(full) > 160:
        parts = re.split(r"(?<=[.!?])\s+", full, maxsplit=1)
        rest = parts[1] if len(parts) > 1 else ""
        if rest:
            out.append(f"Look up the {a1} information that {pk} keeps for a specific {main_param}. {rest}")
            out.append(f"Read-only {pk} view of {a1} data for one {main_param}. {rest}")
        out.append(f"{full} Applies only to {pk}'s own {a1} records; data owned by "
                   f"other products is never returned.")
        out.extend(skeletons[:4])
    else:
        out.extend(skeletons)
    return [_dedupe_words(t) for t in dict.fromkeys(out)]


def build_suggestions(draft: dict, other: dict, product_key: str,
                      taken: Set[str], name_collision: bool,
                      threshold: float = 0.5,
                      others: Optional[List[dict]] = None) -> dict:
    """Up to 3 pickable options per field (name / title / description). Every
    option is VERIFIED with the real scorer against every nearby tool (not just
    the current top match) and offered only if its worst-case overall lands
    below the threshold — an option we show is an option that fixes it."""
    rr = reranker()
    neural = not isinstance(rr, NoopReranker)
    other_name = other.get("name", "")
    current_sim = name_similarity(draft.get("name", ""), other_name)
    field = [other] + [o for o in (others or []) if o is not other]
    current_overall = blend_breakdown(rr, draft, other)["overall"] if neural else None

    def predicted(patch: dict) -> Optional[float]:
        # worst case across ALL nearby tools: a rewrite that dodges the top
        # match but still collides with the #2 must not be called a fix
        if not neural:
            return None
        applied = {**draft, **patch}
        return round(max(blend_breakdown(rr, applied, o)["overall"] for o in field), 2)

    def fixing(options):
        if not neural:
            return options[:3]
        kept = [o for o in options if o["new_overall"] is not None
                and o["new_overall"] < threshold]
        return sorted(kept, key=lambda o: o["new_overall"])[:3]

    names = []
    if name_collision:
        for n in suggest_names(draft, other, product_key, taken):
            if name_similarity(n, other_name) > current_sim:
                continue                          # must not be MORE name-alike
            names.append({"name": n, "title": humanize(n),
                          "new_overall": predicted({"name": n, "title": humanize(n)})})
    names = fixing(names)

    d = _distinct_tokens(draft, other)
    title_texts = [humanize(n["name"]) for n in names]
    if d:
        title_texts.append(f"{humanize(draft.get('name', ''))} ({d[0]})")
        if len(d) > 1:
            title_texts.append(f"{d[0].capitalize()} {d[1]} — {humanize(draft.get('name', ''))}")
    titles = fixing([{"title": t, "new_overall": predicted({"title": t})}
                        for t in dict.fromkeys(title_texts) if t != draft.get("title")])

    descriptions = fixing([{"text": t, "new_overall": predicted({"description": t})}
                              for t in _rewrite_candidates(draft, other, product_key)])

    resolution_hint = None
    if (neural and current_overall is not None and current_overall >= threshold
            and not (names or titles or descriptions)):
        if True:
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
