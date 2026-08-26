# AI Registry + MCP SDK

Runtime-managed registry for MCP tools and A2A-ready agents: metadata for multi-product companies. Edit a tool's
description, schema, audiences or auth scopes in the UI — every running MCP
server picks it up in seconds, no redeploy.

```
backend/       FastAPI registry: RBAC, products, audiences, versioning,
               Redis cache + per-product pub/sub, semantic similarity
ui/            React SPA (detachable — talks only to the REST API)
contracts/     Versioned JSON Schemas: manifest + change events (the SDK contract)
integration/   SDK <-> registry contract tests (SDK installed from its own repo)
scripts/       similarity benchmark etc.
```

The **MCP SDK is a separate repository** (`../mcp-sdk` — `yourco-mcp` package):
product teams install it independently; this repo owns the contract it implements.

## Quick start (Docker)

```bash
docker compose up -d        # UI :5173, API :8000, Postgres, Redis
```

## Quick start (local dev)

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cd backend && ../.venv/bin/python -m uvicorn app.main:app --port 8000   # API :8000
cd ui && npm install && npm run dev                                     # UI  :5173
```

Sign in with the bootstrap super admin `admin@registry.dev` / `admin`
(override via `REGISTRY_BOOTSTRAP_ADMIN_EMAIL` / `..._PASSWORD`; change in prod).

Then: onboard a product → add audiences → create tools → issue an SDK API key
(product Settings tab) → run `examples/billing_server.py` with it.

## Architecture decisions of record

- **Control plane vs data plane.** MCP servers serve every request from an
  in-memory manifest (L1). The registry is contacted only at bootstrap and via
  pub/sub deltas; if it is down, servers run on last-known-good (disk snapshot).
- **Three-tier reads.** SDK memory → Redis (optional, `REGISTRY_REDIS_URL`) →
  Postgres/SQLite. The same publish that notifies SDKs invalidates the cache.
- **Sequence-numbered events.** Gap detected → full re-fetch. Convergence is
  guaranteed, not hoped for. See `contracts/event.schema.json`.
- **Audiences (external / internal / …).** Base ⊕ overlay resolved at write
  time in ONE place (the registry). Overlays: override description/title/
  annotations, `add` / `modify` / `hide+pin` parameters, or disable per
  audience. The `x-tool-audience` header *requests*; the caller's
  `audience:<key>` scope *grants*; everyone else is downgraded to the default.
  Pinned values are injected server-side and always beat caller arguments.
- **Validation pipeline.** Shape → semantic checks on the resolved result →
  atomic commit → publish. Field-level errors (`path`, `code`, `message`) pin
  to exact UI rows; the UI live preview calls the same dry-run code path.
- **Versioning.** Every save is a version; rollback restores an old payload as
  a NEW version (the undo path is the normal path).
- **Default auth in the SDK.** `tools/list` open, everything else gated.
  Loosening requires the explicit `NoAuth()` provider. Per-tool
  `auth.required_scopes` live in the registry and are enforced by the SDK;
  an optional `@server.authorize` hook adds code-level checks.
- **Similarity.** Hybrid embedding + lexical rank with RRF; local hashing
  embedder by default, pluggable (fastembed / any OpenAI-compatible API via
  `REGISTRY_EMBEDDING_PROVIDER`, `REGISTRY_EMBEDDING_API_KEY`). Duplicates
  report within and across products.
- **Generic entities.** `type = tool | agent` end-to-end — the Agent Registry
  (A2A agent cards) is an entity type, not a rewrite.

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest tests -q     # unit + API tests
../.venv/bin/python -m pytest integration -q             # SDK contract tests
../.venv/bin/ruff check --config backend/ruff.toml backend/app   # lint gate
```

The MCP SDK lives at [amitmohapatra/mcp-sdk](https://github.com/amitmohapatra/mcp-sdk);
`integration/` here runs its e2e suite against this registry (also in CI).
