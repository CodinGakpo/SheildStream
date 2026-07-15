# ShieldStream — Architectural Decision Log

Maintained incrementally as decisions are made, not written retroactively. Each entry: Context, Alternatives Considered, Decision, Trade-off Accepted.

---

## Phase 0 — Repository layout & local stack

**Context:** Needed a local dev environment matching the eventual production deployment (Twelve-Factor Factor X: dev/prod parity), with zero manual coordination on startup order.

**Alternatives considered:** Bare-metal local installs of Postgres/Redis (rejected: breaks dev/prod parity, "works on my machine" risk). Minikube/local Kubernetes (rejected: massive operational overhead for a 5-service stack).

**Decision:** Docker Compose with explicit `healthcheck` blocks and `depends_on: condition: service_healthy` on every cross-service dependency. Monorepo layout (gateway/, consumers/, dashboard/, db/, infra/, loadtest/ under one repo).

**Trade-off accepted:** None significant at this scale — monorepo starts paying an integration-friction cost only once independent teams need independent release cadences, which doesn't apply to a solo project.

---

## Phase 1 — Schema, RLS & migrations

### Revision #1 — API key hashing: SHA-256, not bcrypt

**Context:** The original guide bcrypt-hashes the tenant API key and, on a cache miss, checks the presented key against *every* tenant's bcrypt hash (no indexed lookup, since bcrypt hashes aren't comparable via equality on the raw key). At bcrypt cost factor 12 (~100ms/check), this is O(n tenants × 100ms) on every cold lookup — and structurally can't be indexed at all, since bcrypt is deliberately non-deterministic per-call (salted).

**Alternatives considered:** Keep bcrypt but add an unindexed linear scan (guide's approach, rejected: doesn't scale past a handful of tenants and is slow even then). Store the key hash as bcrypt but *also* store a separate lookup prefix (rejected: adds a second field and a still-approximate lookup for no benefit over a proper hash).

**Decision:** `api_key_hash = SHA-256(raw_key)`, stored with a `UNIQUE` constraint, looked up via `WHERE api_key_hash = $1` (a single indexed equality lookup, O(log n)). Comparison is inherently exact-match, not timing-attack-prone in the way a manual `==` would be, since it's a database index lookup, not an in-process string comparison.

**Trade-off accepted:** Bcrypt's slow-by-design property exists specifically to blunt offline brute-forcing of *low-entropy* secrets (human passwords). A randomly generated 128-bit `sk_test_<32 hex chars>` API key has no brute-forceable structure for bcrypt's slowness to defend against — the entropy itself is the defense. This is the same reasoning industry API-key schemes (Stripe, GitHub PATs) use: hash for at-rest protection against a DB leak, not for brute-force resistance, because the key space is already too large to brute force.

### Revision #2 — Missing tenant columns

**Context:** The guide's Week 3 proxy handler reads `tenant['upstream_base_url']` and Week 6's event emitter reads `tenant['salt']`, but neither column exists in the Week 2 schema as written.

**Decision:** Added `upstream_base_url TEXT NOT NULL` and `ip_hash_salt TEXT NOT NULL DEFAULT encode(gen_random_bytes(16), 'hex')` to `tenants` in the initial migration.

### Revision #4 — RLS is bypassed by the table owner (and any BYPASSRLS role)

**Context:** PostgreSQL's Row-Level Security is inert for the table's owning role and any role with the `BYPASSRLS` attribute, regardless of defined policies — a fact the guide's own "Common Pitfalls" section for Week 2 warns about ("connecting as a Postgres superuser... bypasses RLS entirely") but doesn't fully act on: the guide never introduces a non-owner application role, so its own RLS verification test (run via `psql -U shieldstream`, the owner) would silently pass even if RLS were completely broken.

**Alternatives considered:** Rely on `ENABLE ROW LEVEL SECURITY` alone and just "remember" to connect as a non-owner role during testing (rejected: exactly the kind of implicit discipline RLS exists to remove — see the guide's own §4 conceptual framing). Application-layer `WHERE tenant_id = ?` filtering only, no RLS (rejected per the guide's original reasoning: relies on every engineer, on every query, forever).

**Decision:** Two non-owner roles, created in the same migration as the schema:
- `shieldstream_app` (gateway runtime): RLS-bound, granted only `SELECT` on `tenants`/`policies` and `SELECT/INSERT/UPDATE` on `request_metrics`.
- `shieldstream_worker` (analytics/alert consumers, admin policy API): `BYPASSRLS`, because these are trusted internal processes that batch-write `request_metrics` rows spanning many tenants in a single upsert (Week 7's micro-batching) — structurally incompatible with a single session-scoped `current_setting('app.tenant_id')`.

`request_metrics` additionally gets `FORCE ROW LEVEL SECURITY` (not just `ENABLE`), so the policy applies even to the table owner — the RLS verification test connects as `shieldstream_app`, the role actually meant to be governed by the policy, not as the migration/owner role.

**Trade-off accepted:** `shieldstream_worker`'s `BYPASSRLS` means a bug in consumer code *could* cross-write another tenant's rows undetected by the database layer — the guarantee RLS provides is scoped to `shieldstream_app`'s query path (present and future tenant-facing reads), not to the trusted internal write path. This mirrors the guide's own Week 2 note that the analytics consumer is "the only writer to `request_metrics`" — a single, audited writer is the actual control here, not RLS.

### Revision #5 — Alembic transaction conflicts

**Context:** `CREATE INDEX CONCURRENTLY` and TimescaleDB's `CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous)` both have restrictions around running inside an explicit transaction block, but Alembic wraps every migration in one by default.

**Decision:** Used a plain `CREATE INDEX` (not `CONCURRENTLY`) for the Week 2 index — safe specifically because this is the initial migration against an empty, just-created table with no live traffic or lock-contention cost to avoid (the guide's own stated reason for `CONCURRENTLY` is protecting a *live* table). For the continuous aggregate, explicitly issued `COMMIT` before the `CREATE MATERIALIZED VIEW` statement and `BEGIN` after, so it runs outside Alembic's managed transaction while leaving an empty transaction for Alembic's own trailing commit to close harmlessly.

**Trade-off accepted:** The raw `COMMIT`/`BEGIN` inside `upgrade()` is a known but inelegant workaround — a future migration that touches `CONCURRENTLY` on a live table will need the same pattern (or Alembic's `with op.get_bind().execution_options(isolation_level="AUTOCOMMIT")` alternative), and it must be applied deliberately each time, not assumed.

### Revision #6 — TimescaleDB continuous aggregates are incompatible with RLS (discovered running the migration, not from docs)

**Context:** Running the Week 2 migration as written failed with `psycopg2.errors.FeatureNotSupported: cannot create continuous aggregate on hypertable with row security`. This is a hard TimescaleDB restriction, not a transaction-scoping issue (revision #5's autocommit workaround doesn't touch it at all): a continuous aggregate's incremental refresh runs as a background worker with no request-session context, so it has no `app.tenant_id` to evaluate the RLS policy against. TimescaleDB refuses to create the aggregate outright rather than risk materializing rows across tenants unfiltered.

**Alternatives considered:** Drop RLS from `request_metrics` to unblock the continuous aggregate (rejected: undoes revision #4's structural multi-tenant isolation guarantee for the one table where cross-tenant leakage would matter most). Create the continuous aggregate on a separate non-RLS shadow table fed by a trigger (rejected: adds a second write path and trigger-maintenance surface for the same effect the worker's own upsert already achieves more simply).

**Decision:** `request_metrics_hourly` is a plain table (same RLS treatment as `request_metrics` — `ENABLE`/`FORCE ROW LEVEL SECURITY` + the same `tenant_isolation` policy), populated by an explicit `INSERT ... ON CONFLICT DO UPDATE` upsert run periodically by `shieldstream_worker` (already `BYPASSRLS`, already the only writer to `request_metrics` per Week 7's design) — built alongside the Week 9 dashboard in Phase 5, since nothing reads it before then.

**Trade-off accepted:** Loses TimescaleDB's automatic incremental refresh (the continuous aggregate only recomputes what changed); the replacement upsert recomputes over whatever window each refresh cycle covers. For this project's data volume this is a non-issue — it's the same trade the guide already made peace with for Neon in the Phase 6 plan (continuous aggregates are TSL-licensed and unavailable there anyway), just arriving one phase earlier than expected.

---

## Phase 1 — Verified

- `alembic upgrade head` / `downgrade -1` / `upgrade head` round-trip cleanly against a fresh database.
- `timescaledb_information.hypertables` confirms `request_metrics` is a hypertable.
- Seed script creates two tenants (`acme-corp`, `globex-inc`) with distinct SHA-256 key hashes and default `/proxy/*` policies.
- RLS test: connected as `shieldstream_app` with `app.tenant_id` set to acme's ID, a bare `SELECT * FROM request_metrics` (no `WHERE`) returns only acme's row — globex's row, inserted moments earlier, is invisible. Connected as `shieldstream_worker`, the same query returns both tenants' rows, confirming `BYPASSRLS` works as designed for the internal batch-write path.

---

## Phase 0/1 — Local environment quirks (this machine, not portable guidance)

- **Container DNS is broken on this host's default bridge network** (container `resolv.conf` points at an upstream DNS IP unreachable from `docker0`, likely a firewalld/systemd-resolved interaction). Fixed by building with `network: host` on affected build stages (`gateway`, `db`/migrate) instead of touching the Docker daemon config or firewalld, which would have disrupted other unrelated running containers on this machine.
- **SELinux (Fedora, Enforcing) blocks bind-mounted host directories by default** — the gateway silently got `Permission Denied` reading `/app/app` until the mount was labeled `:Z` in `docker-compose.yml`.
- **Host Python is 3.14, not 3.12** — no local venv matches the pinned runtime, so Alembic/seed/tests that need the pinned version run inside Docker (`db/Dockerfile`, `python:3.12-slim`). A host venv (`gateway/.venv`) is still useful for fast pure-Python unit-test iteration (e.g. the Week 4 Lua rate-limiter proof) since `fakeredis[lua]` and friends install cleanly on 3.14 too — but anything asserting the pinned interpreter version runs in a container.

---

## Phase 2 — Reverse proxy, auth, tracing

### Bug found during load testing — eager DB dependency serialized requests under concurrency

**Context:** `get_tenant`'s original signature declared `db: AsyncSession = Depends(get_db_session)`. FastAPI resolves every `Depends()` parameter before the function body runs, regardless of whether the body's control flow ever uses it — so every request acquired a pooled Postgres connection, even on a Redis cache hit, which never touches the database. First measured as: 2.6ms p50 at concurrency 1, ballooning to ~47ms p50 at concurrency 20 on `/proxy/get`, while the DB-free `/health` endpoint stayed fast (5.9ms p50) at the same concurrency — isolating the cost to the auth path specifically.

**Decision:** Removed the `Depends()` parameter; `auth.py` now calls `session_factory()` (exported from `app/db.py`) directly, only inside the cache-miss branch. `get_db_session` stays available as a FastAPI dependency for routes that unconditionally need a session.

**Trade-off accepted:** None — this is a strict improvement with no downside; the guide's own Week 3 code has the same latent bug (it also declares `db = Depends(get_db_session)` unconditionally in `get_tenant`), just never surfaced it because the guide's testing section doesn't run a concurrent benchmark against a cache-warm gateway.

### Finding — `kennethreitz/httpbin` is not a valid concurrency baseline for the proxy-overhead measurement

**Context:** After the fix above, `/proxy/get` at concurrency 20 was still ~37ms p50 — investigated further rather than accepted at face value. Isolated by hitting `httpbin` directly via `httpx`, concurrently, from inside the gateway container (bypassing ShieldStream entirely): 200 concurrent requests to `httpbin:80/get` gave p50 ≈ 200ms. `kennethreitz/httpbin` runs a single/few-worker gunicorn dev server — it serializes concurrent requests on its own, unrelated to anything ShieldStream does. The guide names this exact image as the Week 3 downstream test double without flagging this limitation.

**Decision:** For proxy-overhead verification, measure the *delta* between proxied and direct requests at a concurrency level `httpbin` can actually sustain without becoming the bottleneck (empirically, ~5), not at the higher concurrency intended for later load-testing phases (Week 10/11, which will need a real or at least multi-worker downstream target).

**Trade-off accepted:** At c=5: proxied p50 = 8.4ms vs direct p50 = 2.3ms (Δ ≈ 6.1ms, within the guide's <8ms target); p99 delta is closer to 10ms, slightly over. This is an honest, defensible number for Week 3's scope — it is not the number to use for the real Week 10/11 load test, where `httpbin` will need to be swapped for a downstream that can actually take load (documented here so it isn't forgotten).

---

## Phase 2 — Verified

- Proxied request with a seeded API key forwards to `httpbin` and returns its response unmodified; `Host` header correctly shows the downstream's own value, not ShieldStream's.
- Invalid API key → `401 {"detail":"invalid API key"}` before any downstream call.
- `X-Api-Key` is stripped before forwarding (tightening beyond the guide, which forwards it) — verified the downstream never sees it.
- Redis cache populated on first request (`tenant:apikey:<sha256>`, 30s TTL), confirmed via `redis-cli`.
- Single trace per request visible in Jaeger, with `auth.validate_key` and `proxy.forward` correctly nested as children of the auto-instrumented root span; a 401 trace correctly shows only `auth.validate_key` with no `proxy.forward` child.
- Proxy overhead at a concurrency `httpbin` can sustain (c=5): ~6ms delta, within the guide's <8ms p99 target (see finding above for the c=20 caveat).

---

*(Phases 3+ decisions appended as each phase is implemented.)*
