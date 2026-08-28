"""Central tuning knobs for similarity and suggestions.

Nothing behavioral is hard-coded at call sites: every knob lives here with a
named default and can be overridden AT RUNTIME by super admins through the
global settings row (Manage -> Similarity), making behavior meta-driven.
Unknown or non-numeric overrides are ignored, so a bad settings row can never
break scoring.
"""

DEFAULTS = {
    "similarity_threshold": 0.5,   # flagging bar, used uniformly everywhere
    "candidate_top_k": 10,         # nearby tools every suggestion is verified against
    "packages_max": 3,             # resolution packages offered
    "per_field_max": 3,            # single-field options kept per field
    "long_desc_chars": 160,        # above this, descriptions are EDITED, never replaced
    "retention_min": 0.6,          # min share of content words an edit must keep
    "max_sentence_edits": 3,       # surgical-edit budget in the greedy loop
    "name_collision_sim": 0.8,     # name similarity that alone triggers suggestions
}


def tuning(overrides: dict | None = None) -> dict:
    t = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k in t and isinstance(v, (int, float)) and not isinstance(v, bool):
            t[k] = v
    return t
