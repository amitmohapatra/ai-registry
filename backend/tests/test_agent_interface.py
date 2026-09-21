"""An agent's interface: the schemas a planner needs in order to choose it.

A2A's card describes transport and media *modes*, never shapes, so `input_schema` and
`output_schema` are the only place a caller learns what an agent accepts and returns.
They describe the varying part only — `input_schema` is the harness's `AgentRequest.input`
and `output_schema` is its `AgentResponse.data`. The envelope around them (status, claims,
evidence, error) is fixed by the harness contract and identical for every agent.
"""
import copy

from tests.conftest import login, make_product

AGENT = {
    "name": "refund-agent",
    "description": "Decides and issues customer refunds.",
    "input_schema": {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "Order to refund"}},
        "required": ["order_id"],
    },
    "output_schema": {
        "type": "object",
        "description": "Shape of AgentResponse.data",
        "properties": {"outcome": {"enum": ["refunded", "declined"]}},
        "required": ["outcome"],
    },
    "card": {"capabilities": {"streaming": True}},
}


async def _create(client, su, payload, product="agents"):
    return await client.post(f"/v1/products/{product}/entities",
                             json={"type": "agent", "payload": payload}, headers=su)


async def test_an_agent_interface_survives_into_the_resolved_view(client):
    """Both directions must reach a consumer. output_schema used to be dropped by the
    resolver, so an agent could declare what it returns and nobody would ever see it."""
    su = await login(client)
    await make_product(client, su, "agents")
    r = await _create(client, su, AGENT)
    assert r.status_code == 201, r.text
    spec = r.json()["resolved"]["external"]["spec"]
    assert spec["input_schema"]["properties"]["order_id"]["type"] == "string"
    assert spec["output_schema"]["properties"]["outcome"]["enum"] == ["refunded", "declined"]


async def test_an_invalid_output_schema_is_refused_at_write_time(client):
    """Accepting it silently means the failure surfaces much later, as a planner quietly
    skipping the agent, with nothing pointing at the cause."""
    su = await login(client)
    await make_product(client, su, "agents")
    bad = copy.deepcopy(AGENT)
    bad["output_schema"] = {"type": "not-a-real-type"}
    r = await _create(client, su, bad)
    assert r.status_code == 422, r.text
    assert any(e["path"] == "output_schema" for e in r.json()["detail"]["errors"])


async def test_an_invalid_input_schema_is_still_refused(client):
    su = await login(client)
    await make_product(client, su, "agents")
    bad = copy.deepcopy(AGENT)
    bad["input_schema"] = {"type": "object", "properties": {"x": {"type": "nope"}}}
    r = await _create(client, su, bad)
    assert r.status_code == 422, r.text
    assert any(e["path"] == "input_schema" for e in r.json()["detail"]["errors"])


async def test_a_required_field_with_no_definition_is_caught_in_both_directions(client):
    """A ghost required field is legal JSON Schema and a broken contract: the caller is told
    to send something that has no definition."""
    su = await login(client)
    await make_product(client, su, "agents")
    bad = copy.deepcopy(AGENT)
    bad["output_schema"] = {"type": "object", "properties": {"a": {"type": "string"}},
                            "required": ["a", "ghost"]}
    r = await _create(client, su, bad)
    assert r.status_code == 422, r.text
    assert any(e["path"].startswith("output_schema") for e in r.json()["detail"]["errors"])


async def test_an_agent_needs_no_schemas_at_all(client):
    """They are optional: a conversational agent with a free-text contract is legitimate,
    and requiring a schema would push people into writing a meaningless one."""
    su = await login(client)
    await make_product(client, su, "agents")
    r = await _create(client, su, {"name": "chat-agent", "description": "Answers questions."})
    assert r.status_code == 201, r.text
    assert "output_schema" not in r.json()["resolved"]["external"]["spec"]


async def test_output_schema_is_not_reshaped_by_an_audience(client):
    """Overrides exist to narrow what a caller may *send*. What an entity returns is not the
    caller's to alter, so there is no overlay for it."""
    su = await login(client)
    await make_product(client, su, "agents")
    await client.post("/v1/products/agents/audiences", json={"key": "internal"}, headers=su)
    payload = copy.deepcopy(AGENT)
    payload["audiences"] = {"internal": {"overrides": {"description": "Ledger-aware."}}}
    r = await _create(client, su, payload)
    assert r.status_code == 201, r.text
    resolved = r.json()["resolved"]
    assert resolved["internal"]["spec"]["description"] == "Ledger-aware."
    assert resolved["internal"]["spec"]["output_schema"] == resolved["external"]["spec"]["output_schema"]
