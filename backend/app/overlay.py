"""Audience overlay engine: resolve(base ⊕ overlay) and validate — the single place
merge semantics live. SDKs receive already-resolved views and never re-implement this.

Overlay shape (all keys optional):
{
  "enabled": bool,
  "overrides": {
    "description": str, "title": str, "annotations": {...},
    "parameters": {
      "add":    {name: {<json-schema for the param>, "default": ...}},
      "modify": {name: {<partial json-schema fields to shallow-merge>}},
      "hide":   {name: {"pin": <value injected server-side>}}
    }
  }
}
Validation returns field-level errors: [{"path": "...", "code": "...", "message": "..."}]
so the UI can pin messages to exact form rows. Nothing partial ever commits.
"""
import copy
from typing import Any, Dict, List, Optional

import jsonschema

OVERLAY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "enabled": {"type": "boolean"},
        "overrides": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "description": {"type": "string"},
                "title": {"type": "string"},
                "annotations": {"type": "object"},
                "auth": {"type": "object", "properties": {
                    "required_scopes": {"type": "array", "items": {"type": "string"}}}},
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "add": {"type": "object"},
                        "modify": {"type": "object"},
                        "hide": {"type": "object", "additionalProperties": {
                            "type": "object", "required": ["pin"]}},
                    },
                },
            },
        },
    },
}

TOOL_BASE_SCHEMA = {
    "type": "object",
    "required": ["name", "description"],
    "properties": {
        # the MCP spec leaves names unconstrained, but every real client (Claude,
        # OpenAI function calling) enforces this exact rule — so we do too, and
        # nothing stricter
        "name": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,64}$"},
        "title": {"type": "string"},
        "description": {"type": "string", "minLength": 1},
        "input_schema": {"type": "object"},
        "annotations": {"type": "object"},
        "auth": {"type": "object"},
        "audiences": {"type": "object"},
    },
}

AGENT_BASE_SCHEMA = {
    "type": "object",
    "required": ["name", "description"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string", "minLength": 1},
        "card": {"type": "object"},          # A2A agent-card style payload
        "annotations": {"type": "object"},
        "audiences": {"type": "object"},
    },
}

BASE_SCHEMAS = {"tool": TOOL_BASE_SCHEMA, "agent": AGENT_BASE_SCHEMA}


def _err(path: str, code: str, message: str) -> dict:
    return {"path": path, "code": code, "message": message}


def validate_base(entity_type: str, payload: dict) -> List[dict]:
    schema = BASE_SCHEMAS.get(entity_type)
    if schema is None:
        return [_err("type", "unknown_type", f"Unknown entity type '{entity_type}'")]
    errors = [
        _err("/".join(str(p) for p in e.absolute_path) or "payload", "schema", e.message)
        for e in jsonschema.Draft202012Validator(schema).iter_errors(payload)
    ]
    input_schema = payload.get("input_schema")
    if input_schema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(input_schema)
        except jsonschema.SchemaError as e:
            errors.append(_err("input_schema", "invalid_json_schema", e.message))
    return errors


def validate_overlay_shape(audience: str, overlay: dict) -> List[dict]:
    return [
        _err(f"audiences/{audience}/" + "/".join(str(p) for p in e.absolute_path),
             "schema", e.message)
        for e in jsonschema.Draft202012Validator(OVERLAY_SCHEMA).iter_errors(overlay)
    ]


def resolve(entity_type: str, payload: dict, audience: str) -> Optional[dict]:
    """base ⊕ overlay -> resolved view for one audience. O(fields+params).
    Returns {"enabled", "spec": {...}, "pins": {...}} or None if disabled."""
    overlay = (payload.get("audiences") or {}).get(audience, {})
    if overlay.get("enabled") is False:
        return {"enabled": False}
    ov = overlay.get("overrides", {})

    spec: Dict[str, Any] = {
        "name": payload["name"],
        "title": ov.get("title", payload.get("title", payload["name"])),
        "description": ov.get("description", payload.get("description", "")),
        "annotations": {**payload.get("annotations", {}), **ov.get("annotations", {})},
        "auth": {**payload.get("auth", {}), **ov.get("auth", {})},
    }
    if entity_type == "agent":
        spec["card"] = copy.deepcopy(payload.get("card", {}))

    schema = copy.deepcopy(payload.get("input_schema") or {"type": "object", "properties": {}})
    props: Dict[str, Any] = schema.setdefault("properties", {})
    required = set(schema.get("required", []))
    pins: Dict[str, Any] = {}

    params = ov.get("parameters", {})
    for name, pspec in params.get("add", {}).items():
        props[name] = copy.deepcopy(pspec)
    for name, patch in params.get("modify", {}).items():
        if name in props:
            props[name] = {**props[name], **copy.deepcopy(patch)}
    for name, h in params.get("hide", {}).items():
        props.pop(name, None)
        required.discard(name)
        pins[name] = h.get("pin")

    schema["required"] = sorted(required & set(props))
    if not schema["required"]:
        schema.pop("required", None)
    schema.setdefault("additionalProperties", False)
    spec["input_schema"] = schema
    return {"enabled": True, "spec": spec, "pins": pins}


def validate_resolved(entity_type: str, payload: dict, audience: str) -> List[dict]:
    """Semantic checks on base ⊕ overlay — the checks that make overrides un-breakable."""
    errors: List[dict] = []
    prefix = f"audiences/{audience}"
    overlay = (payload.get("audiences") or {}).get(audience, {})
    ov = overlay.get("overrides", {})
    params = ov.get("parameters", {})
    base_props = (payload.get("input_schema") or {}).get("properties", {})
    base_required = set((payload.get("input_schema") or {}).get("required", []))

    for name in params.get("modify", {}):
        if name not in base_props:
            errors.append(_err(f"{prefix}/parameters/modify/{name}", "unknown_param",
                               f"Parameter '{name}' does not exist in the base schema"))
    for name in params.get("add", {}):
        if name in base_props:
            errors.append(_err(f"{prefix}/parameters/add/{name}", "duplicate_param",
                               f"Parameter '{name}' already exists in the base schema; use modify"))
    for name, h in params.get("hide", {}).items():
        if name not in base_props:
            errors.append(_err(f"{prefix}/parameters/hide/{name}", "unknown_param",
                               f"Parameter '{name}' does not exist in the base schema"))
            continue
        pin = h.get("pin")
        try:
            jsonschema.validate(pin, base_props[name])
        except jsonschema.ValidationError as e:
            errors.append(_err(f"{prefix}/parameters/hide/{name}/pin", "invalid_pin",
                               f"Pinned value does not satisfy the parameter schema: {e.message}"))
        except jsonschema.SchemaError:
            pass
    # required base params stay callable by construction: only "hide" removes a param,
    # and "hide" requires a pin at the shape level — the handler always receives a value.
    _ = base_required
    view = resolve(entity_type, payload, audience)
    if view and view.get("enabled"):
        try:
            jsonschema.Draft202012Validator.check_schema(view["spec"]["input_schema"])
        except jsonschema.SchemaError as e:
            errors.append(_err(f"{prefix}/input_schema", "invalid_resolved_schema", e.message))
    return errors


def resolve_all(entity_type: str, payload: dict, audience_keys: List[str]) -> Dict[str, dict]:
    """Every audience's resolved view, computed once at write time."""
    return {a: resolve(entity_type, payload, a) for a in audience_keys}


def validate_entity(entity_type: str, payload: dict, audience_keys: List[str]) -> List[dict]:
    """Full pipeline: base shape -> overlay shapes -> semantic checks per audience."""
    errors = validate_base(entity_type, payload)
    if errors:
        return errors
    for aud, overlay in (payload.get("audiences") or {}).items():
        if aud not in audience_keys:
            errors.append(_err(f"audiences/{aud}", "unknown_audience",
                               f"Audience '{aud}' is not defined for this product"))
            continue
        errors.extend(validate_overlay_shape(aud, overlay))
    if errors:
        return errors
    for aud in (payload.get("audiences") or {}):
        errors.extend(validate_resolved(entity_type, payload, aud))
    return errors
