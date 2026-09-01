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
- **Two audiences: external (the definition) and internal (a wording overlay).**
  External IS the base: the editor's first tab writes the canonical fields, and
  internal inherits them until its description is overridden. External text
  overrides no longer exist (folded into base by a one-time migration and on
  every write); external **parameter** ops (`add` / `modify` / `hide+pin`)
  remain an API/SDK capability — pinned values are injected server-side and
  always beat caller arguments. The `x-tool-audience` header *requests*; the
  caller's `audience:<key>` scope *grants*; everyone else gets external.
- **Validation pipeline.** Shape → semantic checks on the resolved result →
  atomic commit → publish. Field-level errors (`path`, `code`, `message`) pin
  to exact UI rows; the UI live preview calls the same dry-run code path.
- **Versioning.** Every save is a version; rollback restores an old payload as
  a NEW version (the undo path is the normal path).
- **Default auth in the SDK.** `tools/list` open, everything else gated.
  Loosening requires the explicit `NoAuth()` provider. Per-tool
  `auth.required_scopes` live in the registry and are enforced by the SDK;
  an optional `@server.authorize` hook adds code-level checks.
- **Similarity: per-view, verified, precomputed.** Retrieval = hybrid
  embedding + lexical rank with RRF; precision = a cross-encoder blend over
  like-for-like fields (description 55 / parameters 20 / name 15 / title 10 —
  contributions always sum to the shown %). Every published *view* (external
  text, internal override) competes as its own variant; overlap rows are view
  combinations with per-side pills. ONE materialized registry-wide report
  serves every surface (Products page, product tabs, editor, status dots) —
  cached by an entity-version fingerprint (stale data is structurally
  impossible; no TTLs), rebuilt in the background on every write, warmed at
  startup. The editor's live check is two-tier (cheap while typing after a
  word boundary; resolution packages fetched lazily when flagged) and
  suggestions are generate-and-tested worst-case against every product with
  meaning preserved (surgical sentence edits + retention guard). All knobs
  live in `app/tuning.py` and are super-admin overridable at runtime.
  The flagging threshold is two-level: a registry default (super admin) and
  an optional per-product override its admins set in Manage → Settings —
  the materialized report is cut at the minimum of all thresholds so every
  product's surfaces filter their own rows from the same scan. One flagging
  rule everywhere: a pair is flagged when the STRICTER of its two owning
  products flags it — a product page shows pairs at its own bar, the global
  Overlaps page shows the union of every product's flagged pairs (each row
  tagged with the bar that caught it), and a tool's Similar tab is its
  product's view scoped to that tool.
- **Generic entities.** `type = tool | agent` end-to-end — the Agent Registry
  (A2A agent cards) is an entity type, not a rewrite.

- **Drafts.** The editor autosaves a per-tool draft (localStorage) that
  survives navigation until published or discarded (whole or per tab), shows
  a published → draft diff, and detects concurrent publishes (a draft records
  the version it was based on).
- **Swappable infrastructure.** Postgres: set `REGISTRY_DATABASE_URL`
  (async SQLAlchemy; schema is Alembic-managed, `alembic upgrade head`).
  Dragonfly (or any Redis-protocol server): set `REGISTRY_REDIS_URL` — the
  bus and cache speak plain Redis protocol, so it is a URL change, no code.
  The API is stateless: scale horizontally behind a load balancer; move the
  in-process preview/report caches to Redis when running multiple replicas.
- **Authorization model.** Three tiers, enforced server-side on every route:
  super admins run the platform (products, people, channels, global settings);
  product admins write within their product (tools, members, audiences, keys);
  members read theirs. The People page is the super admin's org-wide console —
  search, filter by product, grant/change/remove any product role, promote or
  demote super admins (guarded: the last active super admin can never be
  demoted or deactivated — promote a successor first), and deactivate
  accounts. Product admins manage their own team from their product's
  Members tab; super admin is a global flag, never a per-product role. There is NO cross-product access — a product admin who
  hits another product's URL gets a clean access-denied page, and cross-product
  overlap fixes route through a copy-able handoff report instead of a door.
  The registry-wide overlap report and the user directory are the only
  any-authenticated surfaces: they exist precisely for cross-team coordination.
- **Pagination contract.** Every list endpoint takes `limit`/`offset` (and `q`
  where searchable — tools, users, directory); the body stays a plain array
  and the total rides in the `X-Total-Count` header, so clients that ignore
  paging keep working. The UI loads on scroll (IntersectionObserver sentinel)
  — tools, versions, users, the member picker and overlap pairs all fetch
  incrementally; nothing renders unbounded lists.
- **No magic numbers.** Deployment knobs (pool sizes, cache caps, scoring
  concurrency, page sizes) live in `app/config.py`, all `REGISTRY_*`
  env-overridable; similarity behavior knobs live in `app/tuning.py` as
  runtime data the super admin can change over the API; UI timing/paging
  constants live in `ui/src/config.ts`.
- **Error contract.** Validation errors are field-pinned (`path`, `code`,
  friendly `message`, rendered inline under the exact field); unexpected
  errors return a clean 500 with a logged server-side trace — stack traces
  never reach clients.

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest tests -q     # unit + API tests
../.venv/bin/python -m pytest integration -q             # SDK contract tests
../.venv/bin/ruff check --config backend/ruff.toml backend/app   # lint gate
```

The MCP SDK lives at [amitmohapatra/mcp-sdk](https://github.com/amitmohapatra/mcp-sdk);
`integration/` here runs its e2e suite against this registry (also in CI).
