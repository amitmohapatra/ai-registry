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
    """Informative words that make the draft different from the lookalike.
    Candidates come from the description and parameter names (lexical filter);
    the RANKING is semantic when the neural reranker is available: each word is
    scored by the cross-encoder against the other tool's text and the words
    FARTHEST from its meaning rank first — 'customer paid' is lexically absent
    from 'payment state' but semantically right next to it, and a purely
    lexical picker cannot see that. Fallback: salience (repeated words first)."""
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
    by_salience = sorted(mine, key=lambda t: (-counts[t], mine.index(t)))
    rr = reranker()
    if not by_salience or isinstance(rr, NoopReranker):
        return by_salience
    try:
        sims = rr.score_pairs(desc_text_of(other), by_salience)
        return [t for _, t in sorted(zip(sims, by_salience), key=lambda x: x[0])]
    except Exception:
        return by_salience


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


def _content_words(text: str) -> set:
    return {t for t in _TOKEN.findall(text.lower())
            if len(t) > 3 and t not in _STOP}


def _retention(candidate: str, original: str) -> float:
    """How much of the original's informational content survives (0..1)."""
    orig = _content_words(original)
    if not orig:
        return 1.0
    return len(_content_words(candidate) & orig) / len(orig)


def _rewrite_candidates(draft: dict, other: dict, product_key: str) -> List[str]:
    """Affirmative rewrites (research: models barely register negation). The
    description is the LLM's behavior contract — a candidate that discards the
    author's content would make every model reading it guess (hallucinate), so
    for substantial descriptions we only produce SURGICAL edits: the sentence
    that collides hardest with the other tool is reworked, everything else is
    kept verbatim, and a retention guard drops any candidate that loses more
    than 40% of the original's content words. Short descriptions carry little
    content and may be rewritten whole, from structurally diverse skeletons."""
    d = _distinct_tokens(draft, other)
    params = list((draft.get("input_schema") or {}).get("properties", {}))
    main_param = (params[0].replace("_", " ") if params else "identifier")
    if not d:
        return []
    a1 = f"{d[0]} {d[1]}" if len(d) >= 2 else d[0]
    a2 = (f"{d[2]} {d[3]}" if len(d) >= 4 else (d[2] if len(d) >= 3 else a1))
    pk = product_key
    full = desc_text_of(draft).strip()
    out: List[str] = []
    if len(full) > 160:
        sentences = re.split(r"(?<=[.!?])\s+", full)
        rr = reranker()
        if len(sentences) > 1 and not isinstance(rr, NoopReranker):
            sims = rr.score_pairs(desc_text_of(other), sentences)
            worst = max(range(len(sentences)), key=lambda i: sims[i])
        else:
            worst = 0
        leads = [
            f"Look up the {a1} information that {pk} keeps for a specific {main_param}.",
            f"{pk.capitalize()}-scoped record view centred on {a1}, keyed by {main_param}.",
            f"{pk.capitalize()} reference for {a2} data tied to one {main_param}.",
        ]
        for lead in leads:                       # rework ONLY the offending sentence
            rebuilt = sentences[:worst] + [lead] + sentences[worst + 1:]
            out.append(" ".join(rebuilt))
        out.append(f"{full} Applies only to {pk}'s own {a1} records; data owned by "
                   f"other products is never returned.")
        out = [t for t in out if _retention(t, full) >= 0.6]
    else:
        out.extend([
            f"{a1.capitalize()} lookup for {pk}: returns the {a1} record matching {_an(main_param)}.",
            f"Read-only access to {pk}'s {a1} data, keyed by {main_param}.",
            f"{pk.capitalize()}-side query that resolves {_an(main_param)} to its {a2} entry.",
            f"Reports the {a2} held by {pk} for one {main_param}; nothing else is returned.",
            f"Look up the {a1} held in {pk} for a specific {main_param}; no other record types are returned.",
        ])
    return [_dedupe_words(t) for t in dict.fromkeys(out)]


def build_suggestions(draft: dict, other: dict, product_key: str,
                      taken: Set[str], name_collision: bool,
                      threshold: float = 0.5,
                      others: Optional[List[dict]] = None) -> dict:
    """Never a dead end. Preference order per field: options VERIFIED to land
    below the threshold against every nearby tool; else the best available
    improvements (honestly labeled). If no single field fixes it, try the
    fields COMBINED (rename + new description together); and always leave the
    author a concrete fill-in template plus the consolidate-or-differentiate
    decision when the tools genuinely duplicate each other."""
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

    def pick(options):
        if not neural:
            return options[:3]
        valid = [o for o in options if o["new_overall"] is not None]
        fixes = [o for o in valid if o["new_overall"] < threshold]
        pool = fixes or [o for o in valid if current_overall is None
                         or o["new_overall"] < round(current_overall, 2)]
        return sorted(pool, key=lambda o: o["new_overall"])[:3]

    raw_names = []
    if name_collision:
        for n in suggest_names(draft, other, product_key, taken):
            if name_similarity(n, other_name) > current_sim:
                continue                          # must not be MORE name-alike
            raw_names.append({"name": n, "title": humanize(n),
                              "new_overall": predicted({"name": n, "title": humanize(n)})})
    names = pick(raw_names)

    d = _distinct_tokens(draft, other)
    title_texts = [humanize(n["name"]) for n in names]
    if d:
        title_texts.append(f"{humanize(draft.get('name', ''))} ({d[0]})")
        if len(d) > 1:
            title_texts.append(f"{d[0].capitalize()} {d[1]} — {humanize(draft.get('name', ''))}")
    titles = pick([{"title": t, "new_overall": predicted({"title": t})}
                   for t in dict.fromkeys(title_texts) if t != draft.get("title")])

    full_desc = desc_text_of(draft).strip()
    raw_desc = [{"text": t, "new_overall": predicted({"description": t}),
                 "keeps_content": _retention(t, full_desc) >= 0.6}
                for t in _rewrite_candidates(draft, other, product_key)]
    descriptions = pick(raw_desc)

    def is_fix(o):
        return o["new_overall"] is not None and o["new_overall"] < threshold
    any_fix = any(is_fix(o) for o in names + titles + descriptions)

    # combined fix: rename + rewrite TOGETHER often clears a bar neither
    # field can clear alone (name 15% + title 10% + description 55% of the blend)
    bundle = None
    if neural and not any_fix and current_overall is not None and current_overall >= threshold:
        nm_c = [o for o in sorted((o for o in raw_names if o["new_overall"] is not None),
                                  key=lambda o: o["new_overall"])[:2]]
        dc_c = [o for o in sorted((o for o in raw_desc if o["new_overall"] is not None
                                   and o.get("keeps_content", True)),
                                  key=lambda o: o["new_overall"])[:2]]
        best = None
        for nm in (nm_c or [None]):
            for dc in (dc_c or [None]):
                patch = {}
                if nm:
                    patch.update({"name": nm["name"], "title": nm["title"]})
                if dc:
                    patch["description"] = dc["text"]
                if len(patch) < 3:
                    continue                       # combos only — singles already tried
                p = predicted(patch)
                if p is not None and (best is None or p < best["new_overall"]):
                    best = {"name": patch["name"], "title": patch["title"],
                            "description": patch["description"], "new_overall": p}
        if best and best["new_overall"] < threshold:
            bundle = best

    # concrete frame the author can fill in — forces the distinguishing facts
    template = None
    if neural and not any_fix and current_overall is not None and current_overall >= threshold:
        template = (f"{product_key.capitalize()}-owned <record type> lookup: returns "
                    f"<exactly what it returns> from <your data source>. Use it when "
                    f"<your situation>; for <the other situation> use {other_name}. "
                    f"Does not <things it never does>.")

    resolution_hint = None
    if neural and current_overall is not None and current_overall >= threshold             and not any_fix and not bundle:
        resolution_hint = (
            f"Two honest paths: if this tool and {other_name} really do the same thing, "
            f"keep {other_name} and drop this one. If they are different, the description "
            f"must say how — fill in the template below with your data source and "
            f"use-case and the score will drop.")

    return {
        "names": names,
        "titles": titles,
        "descriptions": descriptions,
        "bundle": bundle,
        "template": template,
        "current_name_match": round(current_sim, 2),
        "description_tip": None if descriptions else description_tip(draft, other),
        "resolution_hint": resolution_hint,
    }
