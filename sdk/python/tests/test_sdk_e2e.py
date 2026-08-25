"""SDK end-to-end against a live in-process registry: bootstrap, audiences,
default auth, pins, hot reload, seq gaps, outage fallback, ASGI transport."""
import asyncio
import copy
import json

from yourco_mcp import AuthUser, NoAuth, StaticTokenProvider
from .conftest import SECURE_TOOL, TOOL, rpc

INTERNAL = StaticTokenProvider({
    "int-token": {"id": "svc-agent", "scopes": ["audience:internal", "payments:write"]},
    "ext-token": {"id": "some-user", "scopes": []},
})


async def make_started(registry, make_server, **kw):
    await registry.setup_product()
    await registry.add_tool(TOOL)
    server = make_server(**kw)

    @server.tool("get_invoice")
    async def get_invoice(ctx, invoice_id, max_results=100, include_ledger=False):
        return {"invoice_id": invoice_id, "max_results": max_results,
                "include_ledger": include_ledger, "audience": ctx.audience,
                "caller": ctx.user.id}

    await server.start()
    return server


def result_of(resp):
    assert "error" not in resp, resp
    return json.loads(resp["result"]["content"][0]["text"])


# ---------- bootstrap & protocol basics ----------

async def test_initialize_ping_and_list_open_without_auth(registry, make_server):
    server = await make_started(registry, make_server, auth=INTERNAL)
    init = await rpc(server, "initialize")
    assert init["result"]["protocolVersion"] and init["result"]["capabilities"]["tools"]
    assert (await rpc(server, "ping"))["result"] == {}
    # tools/list is OPEN by default: no credentials, external view served
    tools = (await rpc(server, "tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_invoice"]
    assert "max_results" not in tools[0]["inputSchema"]["properties"]   # hidden for external


async def test_execute_gated_by_default(registry, make_server):
    server = await make_started(registry, make_server, auth=INTERNAL)
    resp = await rpc(server, "tools/call", {"name": "get_invoice",
                                            "arguments": {"invoice_id": "i1"}})
    assert resp["error"]["code"] == -32001            # Unauthorized
    resp = await rpc(server, "tools/call", {"name": "get_invoice",
                                            "arguments": {"invoice_id": "i1"}},
                     headers={"authorization": "Bearer wrong"})
    assert resp["error"]["code"] == -32001


async def test_noauth_explicit_optout(registry, make_server):
    server = await make_started(registry, make_server, auth=NoAuth())
    resp = await rpc(server, "tools/call", {"name": "get_invoice",
                                            "arguments": {"invoice_id": "i1"}})
    assert result_of(resp)["invoice_id"] == "i1"

# ---------- audiences ----------

async def test_audience_entitlement_and_spoof_downgrade(registry, make_server):
    server = await make_started(registry, make_server, auth=INTERNAL)
    # entitled caller gets the internal view (extra param, internal description)
    tools = (await rpc(server, "tools/list",
                       headers={"authorization": "Bearer int-token",
                                "x-tool-audience": "internal"}))["result"]["tools"]
    assert "include_ledger" in tools[0]["inputSchema"]["properties"]
    assert tools[0]["description"] == "Fetch invoice incl. ledger refs."
    # spoofed header WITHOUT entitlement silently downgrades to external
    tools = (await rpc(server, "tools/list",
                       headers={"authorization": "Bearer ext-token",
                                "x-tool-audience": "internal"}))["result"]["tools"]
    assert "include_ledger" not in tools[0]["inputSchema"]["properties"]
    # anonymous spoof: same downgrade
    tools = (await rpc(server, "tools/list",
                       headers={"x-tool-audience": "internal"}))["result"]["tools"]
    assert "include_ledger" not in tools[0]["inputSchema"]["properties"]


async def test_pin_injection_and_arg_stripping(registry, make_server):
    server = await make_started(registry, make_server, auth=INTERNAL)
    # external caller: max_results hidden -> pinned 25 injected; sneaky args stripped
    resp = await rpc(server, "tools/call",
                     {"name": "get_invoice",
                      "arguments": {"invoice_id": "i1", "max_results": 999999,
                                    "include_ledger": True, "evil": "x"}},
                     headers={"authorization": "Bearer ext-token"})
    body = result_of(resp)
    assert body["max_results"] == 25                  # pin wins over caller's 999999
    assert body["include_ledger"] is False            # internal-only param stripped
    # internal caller can use the added param and set max_results
    resp = await rpc(server, "tools/call",
                     {"name": "get_invoice",
                      "arguments": {"invoice_id": "i1", "max_results": 7,
                                    "include_ledger": True}},
                     headers={"authorization": "Bearer int-token",
                              "x-tool-audience": "internal"})
    body = result_of(resp)
    assert body == {"invoice_id": "i1", "max_results": 7, "include_ledger": True,
                    "audience": "internal", "caller": "svc-agent"}


async def test_schema_validation_rejects_bad_args(registry, make_server):
    server = await make_started(registry, make_server, auth=NoAuth())
    resp = await rpc(server, "tools/call", {"name": "get_invoice", "arguments": {}})
    assert resp["error"]["code"] == -32602 and "invoice_id" in resp["error"]["message"]
    resp = await rpc(server, "tools/call",
                     {"name": "get_invoice", "arguments": {"invoice_id": 123}})
    assert resp["error"]["code"] == -32602

# ---------- per-tool scopes & authorize hook ----------

async def test_registry_driven_scopes_enforced(registry, make_server):
    await registry.setup_product()
    await registry.add_tool(SECURE_TOOL)
    server = make_server(auth=INTERNAL)

    @server.tool("refund_payment")
    async def refund(ctx, payment_id, amount):
        return {"refunded": amount}

    await server.start()
    call = {"name": "refund_payment", "arguments": {"payment_id": "p1", "amount": 10}}
    denied = await rpc(server, "tools/call", call,
                       headers={"authorization": "Bearer ext-token"})
    assert denied["error"]["code"] == -32003          # Forbidden: missing payments:write
    ok = await rpc(server, "tools/call", call,
                   headers={"authorization": "Bearer int-token"})
    assert result_of(ok) == {"refunded": 10}


async def test_authorize_hook(registry, make_server):
    server = await make_started(registry, make_server, auth=INTERNAL)

    @server.authorize
    async def gate(user, tool, args):
        return args.get("invoice_id") != "forbidden"

    ok = await rpc(server, "tools/call",
                   {"name": "get_invoice", "arguments": {"invoice_id": "fine"}},
                   headers={"authorization": "Bearer int-token"})
    assert "error" not in ok
    denied = await rpc(server, "tools/call",
                       {"name": "get_invoice", "arguments": {"invoice_id": "forbidden"}},
                       headers={"authorization": "Bearer int-token"})
    assert denied["error"]["code"] == -32003

# ---------- hot reload, seq gaps, outage ----------

async def test_hot_swap_on_registry_edit(registry, make_server):
    server = await make_started(registry, make_server, auth=NoAuth())
    assert server._compiled.seq == 1
    v2 = copy.deepcopy(TOOL)
    v2["description"] = "LIVE EDITED description."
    entity_id = server._compiled.raw["entities"][0]["id"]
    await registry.update_tool(entity_id, v2)
    for _ in range(50):                                # event is async; wait briefly
        if server._compiled.seq == 2:
            break
        await asyncio.sleep(0.05)
    assert server._compiled.seq == 2
    tools = (await rpc(server, "tools/list"))["result"]["tools"]
    assert tools[0]["description"] == "LIVE EDITED description."


async def test_new_tool_appears_after_handler_prebound(registry, make_server):
    server = await make_started(registry, make_server, auth=NoAuth())

    @server.tool("void_invoice")                       # handler bound before metadata exists
    async def void_invoice(ctx, invoice_id):
        return {"voided": invoice_id}

    tools = (await rpc(server, "tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_invoice"]
    await registry.add_tool({"name": "void_invoice", "description": "Void an invoice.",
                             "input_schema": {"type": "object",
                                              "properties": {"invoice_id": {"type": "string"}},
                                              "required": ["invoice_id"]}})
    for _ in range(50):
        if server._compiled.seq == 2:
            break
        await asyncio.sleep(0.05)
    tools = (await rpc(server, "tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_invoice", "void_invoice"]


async def test_seq_gap_triggers_full_resync(registry, make_server):
    server = await make_started(registry, make_server, auth=NoAuth())
    assert server._compiled.seq == 1
    # two edits land while we pretend the first event was lost
    entity_id = server._compiled.raw["entities"][0]["id"]
    v2 = copy.deepcopy(TOOL); v2["description"] = "v2"
    v3 = copy.deepcopy(TOOL); v3["description"] = "v3"
    await registry.update_tool(entity_id, v2)
    await registry.update_tool(entity_id, v3)
    # deliver ONLY the seq=3 event to a fresh comparison point: simulate by handing
    # the server an out-of-order future event directly
    await server.handle_event({"seq": 3, "type": "entity.updated", "entity": {}})
    assert server._compiled.seq == 3                   # re-synced from the registry
    tools = (await rpc(server, "tools/list"))["result"]["tools"]
    assert tools[0]["description"] == "v3"


async def test_stale_and_duplicate_events_ignored(registry, make_server):
    server = await make_started(registry, make_server, auth=NoAuth())
    before = server._compiled
    await server.handle_event({"seq": 1, "type": "entity.updated", "entity": {}})
    await server.handle_event({"seq": 0, "type": "entity.deleted", "entity": {}})
    assert server._compiled is before                  # untouched


async def test_registry_outage_serves_snapshot(registry, make_server, tmp_path):
    server = await make_started(registry, make_server, auth=NoAuth())
    await server.stop()
    # new server instance, registry now unreachable -> snapshot keeps it alive
    from yourco_mcp import ProductServer
    from yourco_mcp.client import RegistryClient
    import httpx

    class DeadTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("registry down")

    client = RegistryClient("http://registry", "billing", registry.api_key,
                            snapshot_path=str(tmp_path / "snap.json"),
                            transport=DeadTransport())
    async def no_events(manifest):
        if False:
            yield {}
    client.subscribe = no_events
    survivor = ProductServer("http://registry", "billing", registry.api_key,
                             auth=NoAuth(), client=client)

    @survivor.tool("get_invoice")
    async def get_invoice(ctx, invoice_id, max_results=100, include_ledger=False):
        return {"ok": True}

    await survivor.start()                             # no exception: snapshot used
    tools = (await rpc(survivor, "tools/list"))["result"]["tools"]
    assert tools[0]["name"] == "get_invoice"
    await survivor.stop()


async def test_unbound_registry_tool_not_served(registry, make_server):
    await registry.setup_product()
    await registry.add_tool(TOOL)
    await registry.add_tool({"name": "phantom", "description": "No local handler."})
    server = make_server(auth=NoAuth())

    @server.tool("get_invoice")
    async def get_invoice(ctx, invoice_id, max_results=100, include_ledger=False):
        return {}

    await server.start()
    tools = (await rpc(server, "tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_invoice"]    # phantom excluded, no crash
    resp = await rpc(server, "tools/call", {"name": "phantom", "arguments": {}})
    assert resp["error"]["code"] == -32601

# ---------- stateless ASGI transport ----------

async def test_asgi_streamable_http_stateless(registry, make_server):
    import httpx
    server = await make_started(registry, make_server, auth=NoAuth())
    app = server.build_asgi()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://mcp") as client:
        # two independent requests, no session state between them
        r1 = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                             "method": "initialize", "params": {}})
        assert r1.json()["result"]["serverInfo"]["name"] == "billing"
        r2 = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 2,
                                             "method": "tools/call",
                                             "params": {"name": "get_invoice",
                                                        "arguments": {"invoice_id": "i9"}}})
        assert json.loads(r2.json()["result"]["content"][0]["text"])["invoice_id"] == "i9"
        # batch request
        rb = await client.post("/mcp", json=[
            {"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}])
        assert len(rb.json()) == 2
        # notification -> 202, no body
        rn = await client.post("/mcp", json={"jsonrpc": "2.0",
                                             "method": "notifications/initialized"})
        assert rn.status_code == 202
        assert (await client.get("/healthz")).json()["ok"] is True


async def test_audience_delete_reloads_manifest(registry, make_server):
    """Deleting an audience emits manifest.reload; the SDK re-syncs and stops
    serving that audience's views immediately."""
    server = await make_started(registry, make_server, auth=INTERNAL)
    hdrs = {"authorization": "Bearer int-token", "x-tool-audience": "internal"}
    tools = (await rpc(server, "tools/list", headers=hdrs))["result"]["tools"]
    assert "include_ledger" in tools[0]["inputSchema"]["properties"]   # internal view live
    r = await registry.client.delete("/v1/products/billing/audiences/internal",
                                     headers=registry.su)
    assert r.status_code == 204
    for _ in range(50):
        if server._compiled.seq >= 2 and "internal" not in server._compiled.audiences:
            break
        await asyncio.sleep(0.05)
    # entitled caller now gets the default view — the audience no longer exists
    tools = (await rpc(server, "tools/list", headers=hdrs))["result"]["tools"]
    assert "include_ledger" not in tools[0]["inputSchema"]["properties"]
    assert "max_results" not in tools[0]["inputSchema"]["properties"]  # external pins apply
