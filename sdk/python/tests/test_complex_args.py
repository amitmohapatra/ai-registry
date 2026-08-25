"""Runtime enforcement with complex nested schemas: nested validation errors,
object pins injected at call time, defaults, arrays, enums."""
import json

from yourco_mcp import NoAuth
from .conftest import rpc

COMPLEX = {
    "name": "create_order",
    "description": "Create a customer order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "minLength": 8},
            "items": {
                "type": "array", "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "integer", "minimum": 1},
                        "options": {"type": "object",
                                    "properties": {"gift_wrap": {"type": "boolean"}},
                                    "additionalProperties": False},
                    },
                    "required": ["sku", "qty"],
                    "additionalProperties": False,
                },
            },
            "shipping": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["standard", "express", "overnight"]},
                    "address": {"type": "object",
                                "properties": {"line1": {"type": "string"},
                                               "country": {"type": "string",
                                                           "pattern": "^[A-Z]{2}$"}},
                                "required": ["line1", "country"]},
                },
                "required": ["method"],
            },
            "priority": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            "dry_run": {"type": "boolean", "default": False},
        },
        "required": ["customer_id", "items"],
    },
    "audiences": {
        "external": {"overrides": {"parameters": {"hide": {
            "shipping": {"pin": {"method": "standard",
                                 "address": {"line1": "n/a", "country": "US"}}},
            "priority": {"pin": 3},
        }}}},
        "internal": {},
    },
}

GOOD_ARGS = {
    "customer_id": "cust-12345678",
    "items": [{"sku": "SKU-1", "qty": 2, "options": {"gift_wrap": True}},
              {"sku": "SKU-2", "qty": 1}],
}


async def started_server(registry, factory):
    await registry.setup_product()
    await registry.add_tool(COMPLEX)
    server = factory(auth=NoAuth())

    @server.tool("create_order")
    async def create_order(ctx, customer_id, items, shipping=None, priority=5, dry_run=False):
        return {"customer_id": customer_id, "n_items": len(items),
                "shipping": shipping, "priority": priority, "dry_run": dry_run,
                "audience": ctx.audience}

    await server.start()
    return server


def result_of(resp):
    assert "error" not in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


async def call(server, args, headers=None):
    return await rpc(server, "tools/call",
                     {"name": "create_order", "arguments": args}, headers=headers)


async def test_valid_nested_call_defaults_and_object_pins(registry, make_server):
    server = await started_server(registry, make_server)
    body = result_of(await call(server, GOOD_ARGS))
    assert body["n_items"] == 2
    assert body["dry_run"] is False                     # schema default injected
    assert body["priority"] == 3                        # pinned (external audience)
    assert body["shipping"] == {"method": "standard",   # OBJECT pin injected server-side
                                "address": {"line1": "n/a", "country": "US"}}


async def test_caller_cannot_override_object_pin(registry, make_server):
    server = await started_server(registry, make_server)
    sneaky = dict(GOOD_ARGS, shipping={"method": "overnight",
                                       "address": {"line1": "x", "country": "US"}},
                  priority=10)
    body = result_of(await call(server, sneaky))
    assert body["shipping"]["method"] == "standard"     # pin wins, always
    assert body["priority"] == 3


async def test_nested_validation_errors_are_specific(registry, make_server):
    server = await started_server(registry, make_server)
    cases = [
        (dict(GOOD_ARGS, customer_id="short"), "customer_id"),          # minLength
        (dict(GOOD_ARGS, items=[]), "items"),                           # minItems
        (dict(GOOD_ARGS, items=[{"sku": "S"}]), "qty"),                 # nested required
        (dict(GOOD_ARGS, items=[{"sku": "S", "qty": 0}]), "qty"),       # nested minimum
        (dict(GOOD_ARGS, items=[{"sku": "S", "qty": 1,
                                 "options": {"gift_wrap": "yes"}}]), "gift_wrap"),  # nested type
        (dict(GOOD_ARGS, items=[{"sku": "S", "qty": 1, "bogus": 1}]), "bogus"),     # nested extra
    ]
    for args, needle in cases:
        resp = await call(server, args)
        assert resp.get("error", {}).get("code") == -32602, (args, resp)
        assert needle in resp["error"]["message"], (needle, resp["error"]["message"])


async def test_internal_audience_uses_full_nested_schema(registry, make_server):
    server = await started_server(registry, make_server)
    # internal has no pins: shipping is caller-controlled and validated deeply
    hdrs = {"x-tool-audience": "internal"}
    good = dict(GOOD_ARGS, shipping={"method": "overnight",
                                     "address": {"line1": "1 Main St", "country": "DE"}},
                priority=9)
    body = result_of(await call(server, good, headers=hdrs))
    assert body["shipping"]["method"] == "overnight" and body["priority"] == 9
    # bad nested country pattern rejected for internal too
    bad = dict(good, shipping={"method": "express",
                               "address": {"line1": "1 Main St", "country": "Germany"}})
    resp = await call(server, bad, headers=hdrs)
    assert resp["error"]["code"] == -32602 and "country" in resp["error"]["message"]


async def test_enum_violation_rejected(registry, make_server):
    server = await started_server(registry, make_server)
    hdrs = {"x-tool-audience": "internal"}
    resp = await call(server, dict(GOOD_ARGS, shipping={"method": "teleport"}), headers=hdrs)
    assert resp["error"]["code"] == -32602 and "method" in resp["error"]["message"]


async def test_tools_list_exposes_nested_descriptions(registry, make_server):
    """MCP clients must receive the full nested schema incl. every description."""
    server = await started_server(registry, make_server)
    tools = (await rpc(server, "tools/list",
                       headers={"x-tool-audience": "internal"}))["result"]["tools"]
    schema = tools[0]["inputSchema"]
    assert schema["properties"]["items"]["items"]["required"] == ["sku", "qty"]
    assert schema["properties"]["shipping"]["properties"]["address"]["properties"]["country"]["pattern"] == "^[A-Z]{2}$"
    # external view: pinned params entirely absent from the wire format
    ext = (await rpc(server, "tools/list"))["result"]["tools"][0]["inputSchema"]
    assert "shipping" not in ext["properties"] and "priority" not in ext["properties"]
