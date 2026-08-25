"""Complex/nested schemas through the whole pipeline: creation, per-audience
overlays, description preservation, complex pins, and validation edge cases."""
import copy

from .conftest import login, make_product

# A realistically ugly tool: nested objects, arrays of objects, enums, formats,
# per-parameter descriptions at every level.
COMPLEX = {
    "name": "create_order",
    "title": "Create order",
    "description": "Create a customer order with line items, shipping and payment options.",
    "annotations": {"destructiveHint": True},
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Customer UUID", "minLength": 8},
            "items": {
                "type": "array", "minItems": 1, "maxItems": 100,
                "description": "Line items to order",
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string", "description": "Stock keeping unit"},
                        "qty": {"type": "integer", "minimum": 1, "description": "Quantity"},
                        "options": {
                            "type": "object",
                            "description": "Per-item options",
                            "properties": {
                                "gift_wrap": {"type": "boolean", "description": "Wrap it"},
                                "engraving": {"type": "string", "maxLength": 40,
                                              "description": "Engraving text"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "required": ["sku", "qty"],
                    "additionalProperties": False,
                },
            },
            "shipping": {
                "type": "object",
                "description": "Shipping preferences",
                "properties": {
                    "method": {"type": "string", "enum": ["standard", "express", "overnight"],
                               "description": "Delivery speed"},
                    "address": {
                        "type": "object",
                        "properties": {
                            "line1": {"type": "string", "description": "Street address"},
                            "country": {"type": "string", "pattern": "^[A-Z]{2}$",
                                        "description": "ISO country code"},
                        },
                        "required": ["line1", "country"],
                    },
                },
                "required": ["method"],
            },
            "priority": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5,
                         "description": "Internal fulfilment priority"},
            "dry_run": {"type": "boolean", "default": False,
                        "description": "Validate without creating"},
        },
        "required": ["customer_id", "items"],
    },
}


async def setup(client):
    su = await login(client)
    await make_product(client, su, "orders")
    for aud in ("internal", "partner"):
        await client.post("/v1/products/orders/audiences", json={"key": aud}, headers=su)
    return su


async def test_nested_schema_roundtrip_preserves_everything(client):
    """Every nested constraint and every param description must survive resolution."""
    su = await setup(client)
    r = await client.post("/v1/products/orders/entities",
                          json={"type": "tool", "payload": COMPLEX}, headers=su)
    assert r.status_code == 201, r.text
    for aud in ("external", "internal", "partner"):
        schema = r.json()["resolved"][aud]["spec"]["input_schema"]
        items = schema["properties"]["items"]
        assert items["description"] == "Line items to order"
        assert items["items"]["properties"]["sku"]["description"] == "Stock keeping unit"
        assert items["items"]["properties"]["options"]["properties"]["engraving"]["maxLength"] == 40
        assert items["items"]["required"] == ["sku", "qty"]
        addr = schema["properties"]["shipping"]["properties"]["address"]
        assert addr["properties"]["country"]["pattern"] == "^[A-Z]{2}$"
        assert addr["required"] == ["line1", "country"]
        assert schema["properties"]["shipping"]["properties"]["method"]["enum"] == \
            ["standard", "express", "overnight"]
        assert sorted(schema["required"]) == ["customer_id", "items"]


async def test_modify_nested_param_replaces_subtree_and_merges_top_level(client):
    """modify shallow-merges the PARAM schema: top-level keys merge, a provided
    nested object replaces that subtree — deterministic and previewable."""
    su = await setup(client)
    payload = copy.deepcopy(COMPLEX)
    payload["audiences"] = {"partner": {"overrides": {"parameters": {"modify": {
        "shipping": {"description": "Partner shipping (no overnight)",
                     "properties": {
                         "method": {"type": "string", "enum": ["standard", "express"],
                                    "description": "Delivery speed (partner tier)"}},
                     "required": ["method"]},
        "customer_id": {"description": "Partner-scoped customer UUID"},
    }}}}}
    r = await client.post("/v1/products/orders/entities",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert r.status_code == 201, r.text
    partner = r.json()["resolved"]["partner"]["spec"]["input_schema"]["properties"]
    assert partner["shipping"]["description"] == "Partner shipping (no overnight)"
    assert partner["shipping"]["properties"]["method"]["enum"] == ["standard", "express"]
    assert partner["customer_id"]["description"] == "Partner-scoped customer UUID"
    assert partner["customer_id"]["minLength"] == 8          # merged, not lost
    # other audiences untouched
    ext = r.json()["resolved"]["external"]["spec"]["input_schema"]["properties"]
    assert ext["shipping"]["properties"]["method"]["enum"][-1] == "overnight"


async def test_hide_pin_object_and_valid_complex_pins(client):
    """Pin values can be full objects/arrays and are validated against the
    parameter's nested schema."""
    su = await setup(client)
    payload = copy.deepcopy(COMPLEX)
    payload["audiences"] = {"external": {"overrides": {"parameters": {"hide": {
        "priority": {"pin": 3},
        "shipping": {"pin": {"method": "standard",
                             "address": {"line1": "n/a", "country": "US"}}},
    }}}}}
    r = await client.post("/v1/products/orders/entities",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert r.status_code == 201, r.text
    ext = r.json()["resolved"]["external"]
    assert "shipping" not in ext["spec"]["input_schema"]["properties"]
    assert "priority" not in ext["spec"]["input_schema"]["properties"]
    assert ext["pins"] == {"priority": 3,
                           "shipping": {"method": "standard",
                                        "address": {"line1": "n/a", "country": "US"}}}


async def test_invalid_complex_pins_rejected_with_paths(client):
    su = await setup(client)
    cases = [
        # object pin violating nested enum
        ({"shipping": {"pin": {"method": "teleport"}}},
         "audiences/external/parameters/hide/shipping/pin"),
        # object pin missing nested required (address without country)
        ({"shipping": {"pin": {"method": "standard", "address": {"line1": "x"}}}},
         "audiences/external/parameters/hide/shipping/pin"),
        # integer pin out of range
        ({"priority": {"pin": 99}},
         "audiences/external/parameters/hide/priority/pin"),
    ]
    for hide, want_path in cases:
        payload = copy.deepcopy(COMPLEX)
        payload["audiences"] = {"external": {"overrides": {"parameters": {"hide": hide}}}}
        r = await client.post("/v1/products/orders/entities",
                              json={"type": "tool", "payload": payload}, headers=su)
        assert r.status_code == 422, f"{hide} should have been rejected"
        errs = r.json()["detail"]["errors"]
        assert any(e["code"] == "invalid_pin" and e["path"] == want_path for e in errs), errs
    assert (await client.get("/v1/products/orders/entities", headers=su)).json() == []


async def test_add_complex_param_for_one_audience(client):
    su = await setup(client)
    payload = copy.deepcopy(COMPLEX)
    payload["audiences"] = {"internal": {"overrides": {"parameters": {"add": {
        "fulfilment_overrides": {
            "type": "object", "description": "Warehouse-level overrides",
            "properties": {
                "warehouse_ids": {"type": "array", "items": {"type": "string"},
                                  "description": "Restrict to these warehouses"},
                "force_split": {"type": "boolean", "default": False},
            },
            "default": {},
        }}}}}}
    r = await client.post("/v1/products/orders/entities",
                          json={"type": "tool", "payload": payload}, headers=su)
    assert r.status_code == 201, r.text
    internal = r.json()["resolved"]["internal"]["spec"]["input_schema"]["properties"]
    assert internal["fulfilment_overrides"]["properties"]["warehouse_ids"]["items"]["type"] == "string"
    assert "fulfilment_overrides" not in \
        r.json()["resolved"]["external"]["spec"]["input_schema"]["properties"]


async def test_broken_nested_schema_rejected_at_save(client):
    su = await setup(client)
    bad = copy.deepcopy(COMPLEX)
    bad["input_schema"]["properties"]["items"]["items"]["properties"]["qty"]["minimum"] = "one"
    r = await client.post("/v1/products/orders/entities",
                          json={"type": "tool", "payload": bad}, headers=su)
    assert r.status_code == 422
    assert any(e["code"] == "invalid_json_schema" for e in r.json()["detail"]["errors"])


async def test_dry_run_previews_complex_overlays(client):
    su = await setup(client)
    payload = copy.deepcopy(COMPLEX)
    payload["audiences"] = {"external": {"overrides": {"parameters": {
        "hide": {"shipping": {"pin": {"method": "express"}}}}}}}
    r = await client.post("/v1/products/orders/entities/dry-run",
                          json={"type": "tool", "payload": payload}, headers=su)
    body = r.json()
    assert body["valid"], body
    assert body["resolved"]["external"]["pins"]["shipping"] == {"method": "express"}
    assert "shipping" not in body["resolved"]["external"]["spec"]["input_schema"]["properties"]
