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
         "after", "while", "during", "does", "unknown", "linked", "applied",
         "they", "were", "when", "one", "also"}


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


def _object_part(name: str) -> str:
    """'retrieve_invoice_details' -> 'invoice_details' (drop action verbs)."""
    tokens = name.replace("-", "_").split("_")
    kept = [t for t in tokens if t and not is_action_word(t)]
    return "_".join(kept) or name


def suggest_names(draft: dict, other: dict, product_key: str,
                  taken: Set[str]) -> List[str]:
    """Meaning-safe renames: the tool keeps meaning EXACTLY and gains an
    ownership scope — 'payments_invoice_details' says whose data it is.
    Topic-word suffixes ('_overdue', '_subtotal') are never generated: they
    would change what the tool claims to do."""
    current = draft.get("name", "")
    taken_l = {t.lower() for t in taken}
    out: List[str] = []

    def consider(candidate: str):
        c = candidate.strip("_")
        if (c and c.lower() != current.lower() and c.lower() not in taken_l
                and _NAME_RE.match(c) and c not in out):
            out.append(c)

    pk = product_key.replace("-", "_")
    base = _object_part(current)
    consider(f"{pk}_{base}")          # payments_invoice_details
    consider(f"{pk}_{current}")       # payments_retrieve_invoice_details
    consider(f"{base}_{pk}")          # invoice_details_payments
    parts = current.split("_")
    if len(parts) > 1:
        consider("_".join(parts[1:] + parts[:1]))
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
    pk = product_key
    noun = _object_part(draft.get("name", "")).replace("_", " ") or "record"
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
        leads = [                                # ownership scope, meaning intact
            f"{pk.capitalize()}'s own {noun} record for {_an(main_param)} — the copy of this data that {pk} itself stores and serves.",
            f"Look up the {noun} that {pk} maintains for a specific {main_param}; this is {pk}'s system of record, not another product's.",
            f"{pk.capitalize()}-owned {noun} lookup, keyed by {main_param}.",
        ]
        for lead in leads:                       # rework ONLY the offending sentence
            rebuilt = sentences[:worst] + [lead] + sentences[worst + 1:]
            out.append(" ".join(rebuilt))
        out.append(f"{full} Applies only to {pk}'s own records; data owned by "
                   f"other products is never returned.")
        out = [t for t in out if _retention(t, full) >= 0.6]
    else:
        out.extend([
            f"{pk.capitalize()}'s own {noun} data for {_an(main_param)}; serves only what {pk} itself stores.",
            f"Look up the {noun} that {pk} maintains for a specific {main_param}; {pk}'s system of record, not another product's.",
            f"{pk.capitalize()}-owned {noun} lookup, keyed by {main_param}; no other product's data is returned.",
        ])
    return [_dedupe_words(t) for t in dict.fromkeys(out)]


def _fieldize(sentence: str, label: str) -> str:
    """Compress a prose sentence into a telegraphic field list: every content
    word survives (an LLM still reads exactly what the tool covers) while the
    sentence STRUCTURE — what cross-encoders latch onto — changes completely."""
    words = [t for t in _TOKEN.findall(sentence.lower())
             if len(t) > 3 and t not in _STOP and not is_action_word(t)]
    return (f"{label}: " + ", ".join(dict.fromkeys(words)) + ".") if words else sentence


def _greedy_desc_edit(draft: dict, other: dict, product_key: str,
                      threshold: float, field: List[dict], rr,
                      max_edits: int = 3) -> Optional[str]:
    """Iterative surgical edit: rework the sentence that collides hardest,
    re-measure worst-case against every nearby tool, then remove the next-worst
    DUPLICATED sentence (the ones most like the other tool are the duplicated
    content, not the author's unique content), up to max_edits steps. Returns a
    description verified to land below the threshold while keeping >= 60% of
    the original content words — or None if that is honestly impossible."""
    full = desc_text_of(draft).strip()
    if len(full) <= 160:
        return None
    sentences = re.split(r"(?<=[.!?])\s+", full)
    if len(sentences) < 2:
        return None
    d = _distinct_tokens(draft, other)
    if not d:
        return None
    noun = _object_part(draft.get("name", "")).replace("_", " ") or "record"
    params = list((draft.get("input_schema") or {}).get("properties", {}))
    main_param = (params[0].replace("_", " ") if params else "identifier")
    lead = (f"{product_key.capitalize()}'s own {noun} record for {_an(main_param)} — "
            f"the copy of this data that {product_key} itself stores and serves.")
    current = list(sentences)
    edited: set = set()
    for step in range(max_edits + 1):
        text = _dedupe_words(" ".join(x for x in current if x))
        worst_score, blocker = max(
            ((blend_breakdown(rr, {**draft, "description": text}, o)["overall"], o)
             for o in field), key=lambda x: x[0])
        if worst_score < threshold:
            return text if _retention(text, full) >= 0.6 else None
        if step == max_edits:
            return None
        live = [x for x in current if x]
        sims = rr.score_pairs(desc_text_of(blocker), live)
        order = sorted(range(len(live)), key=lambda i: -sims[i])
        idx = next((current.index(live[j]) for j in order
                    if current.index(live[j]) not in edited), None)
        if idx is None:
            return None
        # first step reworks the worst offender into an anchored lead; later
        # steps COMPRESS offenders into field lists — the content words all
        # survive (no hallucination risk) but the prose structure the
        # cross-encoder matches on is gone
        labels = ["Data included", "Usage notes", "Notes"]
        current[idx] = lead if not edited else _fieldize(current[idx], labels[min(len(edited) - 1, 2)])
        edited.add(idx)
    return None


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
        # verified fixes ONLY — a row that gets to 84% helps nobody
        if not neural:
            return options[:3]
        fixes = [o for o in options if o["new_overall"] is not None
                 and o["new_overall"] < threshold]
        return sorted(fixes, key=lambda o: o["new_overall"])[:3]

    raw_names = []
    if name_collision:
        for n in suggest_names(draft, other, product_key, taken):
            if name_similarity(n, other_name) > current_sim:
                continue                          # must not be MORE name-alike
            raw_names.append({"name": n, "title": humanize(n),
                              "new_overall": predicted({"name": n, "title": humanize(n)})})
    names = pick(raw_names)

    cur_title = draft.get("title") or humanize(draft.get("name", ""))
    title_texts = [f"{cur_title} ({product_key.capitalize()})"] + [humanize(n["name"]) for n in names]
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

    if neural and not any_fix and current_overall is not None and current_overall >= threshold:
        deep = _greedy_desc_edit(draft, other, product_key, threshold, field, rr)
        if deep is not None:
            p = predicted({"description": deep})
            if p is not None and p < threshold:
                descriptions = ([{"text": deep, "new_overall": p,
                                  "keeps_content": _retention(deep, full_desc) >= 0.6}]
                                + descriptions)[:3]
                any_fix = True

    # THE headline: name + title + description applied TOGETHER (name 15% +
    # title 10% + description 55% of the blend clears bars no field can alone)
    bundle = None
    if neural and current_overall is not None and current_overall >= threshold:
        nm_c = sorted((o for o in raw_names if o["new_overall"] is not None),
                      key=lambda o: o["new_overall"])[:2]
        desc_pool = [o for o in raw_desc if o["new_overall"] is not None
                     and o.get("keeps_content", True)]
        desc_pool += [o for o in descriptions if o not in desc_pool]
        dc_c = sorted(desc_pool, key=lambda o: o["new_overall"])[:2]
        best = None
        for nm in (nm_c or [None]):
            for dc in (dc_c or [None]):
                patch = {}
                if nm:
                    patch.update({"name": nm["name"], "title": nm["title"]})
                if dc:
                    patch["description"] = dc["text"]
                if len(patch) < 3:
                    continue                       # combos only — singles shown separately
                p = predicted(patch)
                if p is not None and (best is None or p < best["new_overall"]):
                    best = {"name": patch["name"], "title": patch["title"],
                            "description": patch["description"], "new_overall": p}
        if best and best["new_overall"] < threshold:
            bundle = best

    # concrete frame the author can fill in — forces the distinguishing facts
    template = None
    if neural and not any_fix and not bundle and current_overall is not None and current_overall >= threshold:
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
