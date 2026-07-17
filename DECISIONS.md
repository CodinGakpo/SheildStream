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

## Phase 3a (Week 4) — Atomic sliding-window rate limiter

**Context:** A naive rate limiter (`INCR` + `EXPIRE`, or separate `ZCARD`-then-`ZADD` calls from the application) has one of two flaws: a fixed-window counter allows a 2x burst at the window boundary (full limit in the last second of one window, full limit again in the first second of the next); and even a sliding-window *log* implemented as separate Redis calls from Python has a check-then-act race — two concurrent requests can both read "9 of 10, proceed" before either has recorded its own request, because `MULTI`/`EXEC` guarantees atomic execution of queued commands but not isolation between two separate transactions' read and write phases.

**Alternatives considered:** Token bucket — a genuinely strong algorithm (allows controlled bursting), but its state is a single opaque number, not a replayable log, so "why was I rate limited" has no precise answer; also adds distributed refill-timing complexity not justified here. Fixed window counter — rejected outright for the boundary-burst flaw. `WATCH`-based optimistic locking with client-side retry — works, but adds retry-storm risk under contention and more client-side complexity than one atomic script.

**Decision:** Sliding-window log on a Redis Sorted Set (member = request UUID, score = timestamp ms), with the entire check-then-act sequence (`ZREMRANGEBYSCORE` → `ZCARD` → conditional `ZADD` + `PEXPIRE`) as one Lua script. Redis executes a Lua script to completion as a single atomic unit — no other client's command can interleave — which eliminates the race by construction rather than by careful call ordering. `EVALSHA` (not `EVAL`) sends only the script's hash on the hot path, with a `NoScriptError` → reload-and-retry-once fallback for the case where Redis's script cache was flushed (restart, `SCRIPT FLUSH`) but the app still holds a stale SHA.

**Trade-off accepted:** O(n) memory per key, where n = requests inside the current window — a Sorted Set entry costs roughly 64 bytes, so at meaningful per-IP cardinality and high RPS this stops being viable (documented migration path: approximate counting via HyperLogLog, or token bucket, reserved for exact per-tenant/per-API-key enforcement where cardinality is bounded). Not a concern at this project's scale.

**Verified:**
- 100 concurrent async calls against a limit of 10 → exactly 10 allowed, 90 blocked — proven, not assumed, via `asyncio.gather()` (sequential calls alone would never expose the race).
- That same concurrency proof repeated 50 times via `pytest-repeat`: 50/50 passed, zero flakiness.
- Sliding window correctly resets once the window elapses (time-mocked test) — the exact case a fixed-window counter gets wrong.
- Live Redis (not fakeredis) round-trip latency: p50 = 0.116ms, p99 = 0.206ms — well inside the guide's <1ms/<2ms targets.
- `fakeredis[lua]` (the `lupa`-backed extra) executes the *real* Lua script against a real Lua interpreter, not a hand-rolled reimplementation of its semantics — so the unit tests exercise the actual atomicity guarantee, not a stand-in for it.

**Explicitly not done yet (Week 5, next):** the rate limiter is correct in isolation but not wired into the request path — no middleware, no policy-driven limits from the `policies` table, no 429/`Retry-After` response semantics, no fail-open behavior on Redis unavailability. `check_rate_limit()` exists and is proven; nothing calls it from `routes/proxy.py` yet.

---

## Phase 3b (Week 5) — Rate limiter middleware, policy engine, fail-open

### Middleware position and mechanism

**Context:** The guide implements rate limiting as Starlette `@app.middleware("http")`, reading `request.state.tenant` — but Starlette's middleware chain runs *before* FastAPI resolves a route's `Depends()` parameters. `request.state.tenant` doesn't exist yet at that point; the guide's own code would raise `AttributeError` on its very first request.

**Decision:** Implemented as a FastAPI dependency (`app/middleware/rate_limit.py::enforce_rate_limit`, despite the `middleware/` directory name — kept for structural continuity with the guide, documented as a deliberate deviation in the module's own docstring) that takes `tenant: dict = Depends(get_tenant)` as a sub-dependency. FastAPI resolves `get_tenant` once, then `enforce_rate_limit`, in an explicit, testable order — no reliance on registration-order semantics that (per the guide's own Week 5 pitfall list) are easy to get backwards.

### Policy engine: Redis-cached, Postgres-backed, resilient to cache failure

10s TTL (shorter than auth's 30s tenant-identity cache — an operator tightening a limit during an incident wants that to propagate fast) via `app/policy.py::get_policy`, matching `route_pattern` glob-to-SQL-LIKE with longest-pattern-wins tie-breaking. Cached payload includes `policy_version`, per the guide's own pitfall list, to avoid a cache-schema migration when Week 9's push-based invalidation is built. **Verified live:** dropped `acme-corp`'s limit from 100→2 via a direct `UPDATE`; enforcement caught up within the 10s TTL bound.

### Fail-open, and three real bugs found only by actually killing Redis

The guide's own Week 5 pitfall list warns "testing fail-open only by mocking the Redis client... never by actually killing the container" is insufficient — this played out exactly as warned. Killing the `redis` container live surfaced three real defects that a mocked-exception unit test would never have caught:

1. **`auth.py` had zero Redis error handling.** `get_tenant`'s cache `GET` was unguarded — a Redis outage 500'd every request before the rate limiter's own fail-open logic ever ran. Fixed with the same resilient-cache pattern as `policy.py`: `RedisError` on the cache read falls through to Postgres (auth's actual source of truth, entirely independent of Redis); a failure on the cache-repopulation write is swallowed. This isn't "fail open" in the security sense — auth still genuinely authenticates via Postgres — it's making the cache *layer* resilient, which the rate limiter's fail-open logic assumes has already happened before it gets a turn.

2. **No socket timeout on the Redis client at all.** `Redis.from_url()` had no `socket_connect_timeout`/`socket_timeout`. Against a fully-stopped (not just DNS-broken) container, this meant each request could hang for tens of seconds on an OS-level TCP timeout before a `RedisError` ever surfaced — a "fail-open" that takes 30+ seconds per request to trigger isn't preserving availability, it's replacing a clean failure with a slow one. Fixed: explicit `socket_connect_timeout=0.2, socket_timeout=0.2` on the shared client.

3. **A genuine `uvloop`/`anyio` interaction bug** (not an application bug, but real and reproducible): with `uvloop` installed (uvicorn's default when available via `uvicorn[standard]`), a timed-out Redis connection attempt left the process's async DNS-resolution machinery in a state where the *next, completely unrelated* async DNS lookup (`httpx` resolving the downstream proxy target) also failed — consistently, for the full configured connect timeout (verified at exactly 2002.1ms via a captured Jaeger span, matching the configured `connect=2.0` budget precisely). Isolated by reproducing outside the app entirely: a standalone script under the default asyncio loop never reproduced it; the identical script with `uvloop.install()` reproduced it every time. **Decision:** `uvicorn ... --loop asyncio`, trading uvloop's throughput edge for correctness during exactly the failure scenario this week exists to handle gracefully — a defensible trade for a project whose point is provable correctness under failure, not raw throughput. Not fully root-caused to a single line inside uvloop/anyio's DNS resolver internals (would require bisecting their source), but conclusively isolated to that layer via a controlled reproduction, which is the standard of evidence used throughout this project's debugging.

**Verified end-to-end, live, not mocked:**
- Exceeding the seeded 100 req/60s limit: exactly 100 succeed, requests 101+ return `429` with `Retry-After: 60`, `X-RateLimit-Limit: 100`, `X-RateLimit-Remaining: 0`.
- `X-RateLimit-Limit`/`X-RateLimit-Remaining` present and decrementing correctly on every successful response, not just 429s.
- Killing the `redis` container mid-traffic: after the three fixes above, every request returns `200` (fail-open engaged, bounded at ~1.0s — the cost of three short, sequential cache-miss timeouts across auth/policy/rate-limiter — rather than 500ing or hanging), with `redis_unavailable` / `tenant_cache_unavailable` / `policy_cache_unavailable` cleanly logged for each fallback path taken.
- Restarting `redis`: distributed rate limiting resumes correctly with no manual intervention, no restart of the gateway required.
- Also fixed in passing: `greenlet` (a required transitive dependency of SQLAlchemy's async engine) silently failed to resolve in the host venv despite being present in the Docker image — now pinned explicitly in `requirements.txt` rather than relied on transitively.

---

## Phase 4a (Week 6) — Redis Streams producer: event emission off the critical path

**Context:** Every proxied request must feed the analytics and threat-detection pipelines, but a synchronous write (to Postgres, or even to Redis if awaited inline) puts that storage system's latency on the client's critical path. The design requirement is strict: emission must be *invisible* in the request-latency histogram.

**Alternatives considered:** Synchronous Postgres insert per request (rejected: collapses request latency under load — the DB becomes a back-pressure source on the hot path). Redis List as a queue (rejected: `LPOP` is destructive — two independent downstream readers, Week 7's analytics and Week 8's alerts, would need duplicated writes or a fan-out layer; Streams consumer groups give publish-once/consume-independently natively). Awaiting the XADD inline (rejected: XADD is fast (~0.1ms) but still a Redis round trip on every request, and during a Redis stall it would block responses — the exact coupling this design removes).

**Decision:** `emit_event()` schedules the XADD via `asyncio.create_task()` and returns immediately, with two sharp edges handled explicitly (both are the kind of bug that passes every functional test, then bites in production):
1. **Task garbage collection** — the event loop holds created tasks only weakly; a module-level strong-reference set (`_background_tasks`) prevents a not-yet-finished task from being silently collected/cancelled, removed again via `add_done_callback` on completion.
2. **Silent exceptions** — a fire-and-forget task's exception propagates nowhere; the done-callback inspects `task.exception()` and converts failures into a log line + `shieldstream_event_emit_failures_total` Prometheus counter instead of an invisibly dropped event.

Stream is bounded with `MAXLEN ~ 100,000` (approximate): exact trimming costs a check on every XADD; approximate trims only at radix-tree macro-node boundaries — functionally exact here since consumers drain entries within seconds.

Other calls: the 429 path emits its own event (`rate_limited=1`, `latency_ms=0.0` by convention — no upstream round trip exists to measure; the consumer excludes rate-limited events from latency percentiles) before short-circuiting, so blocked traffic — exactly what a security gateway most needs to see — isn't systematically undercounted. Client IPs are salted-hashed (`SHA-256(ip + per-tenant salt)`, 16 hex chars) at the point of origin: no downstream component can mishandle raw IP data because none ever receives it, and the per-tenant salt makes cross-tenant correlation of one client impossible by construction. `client_ip()` relies on uvicorn's `--proxy-headers` (enabled at deployment, scoped to the trusted proxy) rather than hand-parsing spoofable `X-Forwarded-For` (REVISION #6).

**Verified live:**
- 20 requests → exactly 20 stream entries; all fields present, string-typed, `remote_ip_hash` a 16-char hex digest, never a raw IP.
- 429 responses produce `rate_limited=1` events with `status_code=429`.
- Latency: p50 11.2ms at c=5 with emission enabled (vs 8.4ms Phase 2 baseline) — but Jaeger span breakdown attributes the delta entirely to Week 5's rate-limiter path (`rate_limiter.check` 0.3–2.7ms) and normal variance; **emission itself appears in no span and adds no measurable request-path time**, exactly as designed. (First measurement attempt was discarded: 500 requests against the 100/60s limit meant most responses were fast 429s, silently skewing the latency distribution downward — a good reminder that a "better" number needs its status-code distribution checked before it's believed.)
- Trimming verified experimentally, not by reading the call's arguments: 150k pipelined XADDs with `MAXLEN ~ 100k` → `XLEN` = 100,000.

---

## Phase 4b (Week 7) — Analytics consumer: Streams → TimescaleDB, at-least-once

**Context:** A separate worker process (never a gateway background task — a stalled DB write there would compete with live requests for the same event loop) drains `request_events` into the `request_metrics` hypertable. The headline requirement is durability: kill it mid-flush, restart, and get zero loss and zero double-counting — proven by an actual kill-and-restart test, not asserted.

**Core ordering rule:** `XACK` only *after* the TimescaleDB write commits. ACK-then-write would silently become at-most-once (a crash between them loses the batch). Write-then-ACK means the worst case is re-processing an already-written batch — a duplicate, not a loss — which is why the upsert must be idempotent.

**Idempotent upsert:** `INSERT ... ON CONFLICT (bucket, tenant_id, endpoint, method) DO UPDATE` *increments* (`x = request_metrics.x + EXCLUDED.x`), never overwrites — verified directly (two 300-count batches on the same key → 600, not 300). The composite PK designed back in Week 2 exists exactly for this.

**Deviation from the guide — crash recovery via stable consumer name, not random + XAUTOCLAIM-only.** The guide uses a random per-process consumer name and recovers solely via `XAUTOCLAIM` with a 30s idle threshold, run once at startup. **This is broken, and the guide's own kill-restart test (`sleep 10` then check) would expose it** — I hit it live: after a fast restart, the orphaned entries have been idle far less than 30s, so `XAUTOCLAIM` claims nothing, and the main loop's `XREADGROUP ... >` only sees *never-delivered* messages, never the already-delivered-but-unacked ones. The 500 test entries sat permanently stuck in the PEL (DB stayed 0, `XPENDING` stayed 500).

Fixed with the production-standard pattern:
- **Stable consumer name** (`CONSUMER_NAME` env / container hostname). On restart, the same-named consumer drains *its own* PEL instantly via `XREADGROUP ... 0` (`recover_own_pending`) — no idle wait, zero risk, because reclaiming your own assigned-but-unacked work can never steal a peer's.
- **Periodic `XAUTOCLAIM` (30s idle)** kept as the backstop (`adopt_orphaned_pending`, swept every 15s in the main loop, not just at startup) for the *different* failure mode: a consumer that dies permanently (scaled down, replaced on deploy) whose orphans a surviving consumer must adopt.

**Other decisions:** micro-batch (1000 events or 5s, whichever first — bounded so a large backlog drains as multiple flushes, never one giant lock-holding transaction). Aggregation is a pure, import-light module (`aggregate.py`) so bucketing/counting/percentile/poison-tolerance are unit-tested with zero infrastructure; the thin worker loop around it is covered by the live kill-restart test instead. Poison events (malformed fields) are logged, dropped, and still ACKed — never left to wedge the consumer in a crash-redeliver-crash loop on the same bad entry forever. Rate-limited events are excluded from latency percentiles (their conventional 0.0ms would corrupt p50/p99 during exactly the attack the dashboard is watching). Percentile merging across batches is a count-weighted average (weights derived from `total - blocked`) — **not** a true percentile of the combined distribution (that needs a t-digest sketch); a documented, accepted approximation for a dashboard metric, consistent with the guide's own "approximate aggregation is fine here" stance.

**Verified live, repeatably:**
- Baseline: 500 backlog events → exactly 500 `total_requests` in the DB, `XPENDING` → 0.
- Kill-and-restart, done *correctly* (kill within the 5s batch window so 500 entries are genuinely delivered-but-unflushed — confirmed DB=0/PEL=500 at kill time): restart → `recovered_own_pending count=500` → exactly 500 in DB, 0 pending, 0 duplicates. **Repeated 3×, all PASS.** (First attempt was a false pass — the kill landed after the flush had already completed, so it proved normal operation, not recovery; caught it by checking DB=500 already at kill time and redid it with tight timing.)
- Idempotent increment confirmed directly (600, not 300, on a doubled key).
- Consumer lag under live load: 2000 requests → `XPENDING` drained 999→0 within ~2s, DB reached exactly 2000, no double-count of already-acked prior entries.

---

## Phase 5a (Week 8) — Two-tier alert consumer: OWASP signatures + statistical anomaly

**Context:** A second consumer group, `alert-cg`, reads the *same* `request_events` stream `analytics-cg` already drains, from its own independent offset — the Streams-native publish-once/consume-many pattern. A single gateway XADD now feeds two structurally independent pipelines with zero extra producer work and no data duplication; neither group's lag, backlog, or crash history touches the other's. Proven live: with `analytics-consumer` stopped, 25 new requests pushed `analytics-cg` to `lag=25` while `alert-cg` held `lag=0`, then analytics caught up on restart.

The consumer runs two detection tiers on every event and publishes structured, severity-tagged, deduplicated alerts to the `dashboard:alerts` Redis Pub/Sub channel (nothing consumes it until Week 9's dashboard — verified now via `redis-cli SUBSCRIBE` so the JSON contract is fixed before the consumer of it exists).

**Tier 1 — OWASP signature engine (`rules.py`), HIGH severity.** Regex signatures for SQLi (`UNION…SELECT`, `OR 1=1`, trailing comment, `DROP TABLE`), XSS (`<script`, `onerror=`/`onload=`…, `javascript:`), and path traversal (`../`, single- **and** double-URL-encoded `%2e%2e%2f` / `%252e%252e%252f`). This is signature-based detection — the same approach a basic WAF ruleset takes, and it shares that approach's honest limitation: it catches known literal shapes, and an attacker can obfuscate past a naive regex (alternate encodings, comment-splitting inside a keyword). That's precisely *why* it's paired with Tier 2, and it's stated plainly rather than oversold. Two deliberate hardening/correctness choices beyond the guide:
- **ReDoS safety:** the engine runs on attacker-controlled input by design, so a pattern with nested quantifiers would itself be a denial-of-service vector against the consumer via catastrophic backtracking. Patterns are kept simple, anchored, and linear (`UNION.{1,40}?SELECT`, bounded), and are tested against a 50k-char adversarial string that must complete in <0.5s.
- **Per-field scan, not concatenation (deviation from guide):** the guide concatenates `query_string + " " + user_agent` into one scan target, which lets one field's trailing content defeat another's `$`-anchored pattern (the SQL trailing-comment rule). Scanning the two fields *separately* preserves each pattern's anchoring; a rule fires if any pattern hits either field.

**Tier 2 — EWMA baseline + z-score (`statistical.py`, `rps_window.py`), MEDIUM severity.** A per-endpoint EWMA (α=0.1) learns "normal" RPS with no training data, no model file, operational from the first event; the z-score (deviation / EWMA-std) turns it into a `z > 3.0` threshold. Severity is MEDIUM, not HIGH, on purpose: unlike a signature match, a statistical deviation flags "unusual," not "malicious," and request traffic is often not normally distributed (bursty/Poisson-ish) — so this is a cheap, interpretable heuristic, not a rigorous test, and the severity split says so. Cold-start guard `MIN_SAMPLES_BEFORE_SCORING=20` suppresses scoring until the baseline stabilises (a std estimate from 1–2 samples is noise that makes the next ordinary value read as an enormous spurious spike — verified: no alert during warmup).

*A property worth naming from live testing:* against a flat baseline, a single sharp one-second spike pins the z-score at ≈ 1/√(α(1−α)) ≈ **3.33 regardless of spike magnitude**, because the outlier inflates the EWMA variance estimate in the same update step. So detection reliably fires (3.33 > 3.0) for a spike concentrated in one second, but a spike that *ramps* across several seconds lets the mean chase it and stays sub-threshold — a real, honest sensitivity limit of this estimator (a windowed/robust variance or a separate fast/slow-EWMA ratio would sharpen it; documented, not fixed, at this scope). Verified live: 30s flat baseline (~6 rps) → 173-rps spike scored z=3.33 → `BEHAVIORAL_ANOMALY` published; the *next* second (129 rps) scored z=1.86 and correctly did **not** re-fire.

**Deduplication (`dedup.py`).** Alerts sharing `(alert_type, source)` within a 60s window collapse to one published alert carrying a running `count`, modelled on Alertmanager/PagerDuty — the fix for alert fatigue, which is worst during a real sustained attack (hundreds of scanner probes/sec), exactly when alerts matter most. Verified: a burst of 50 identical SQLi probes → **exactly one** published alert, 49 suppressed. Hardened beyond the guide (revision #12): `source` is an attacker-controlled IP hash, so an attacker rotating source could grow the dedup dict without bound — a memory-exhaustion vector — so expired entries are swept on access (time-gated to ~5s so a burst doesn't pay an O(n) sweep per event). The `statistical._baselines` dict is likewise bounded by evicting endpoints idle beyond 1h.

**Delivery semantics differ from the analytics consumer, deliberately.** Analytics does durable counting → strict write-then-ACK and replays its own pending batch on restart (a duplicate is fine via idempotent upsert; a loss is not). The alert consumer does *timely detection* → an alert is a real-time signal, so re-emitting a minutes-old alert from a replayed pending batch on restart would be misdated noise. So on restart it **drops** (ACKs without re-detecting) its own pending entries, and in steady state it ACKs **every** message once scanned — matched or not (revision #11: forgetting the non-matching majority would grow the PEL unbounded on normal traffic). Missing a handful of alerts across a crash is an accepted trade against double-alerting; a sustained attack re-triggers on the next live event anyway.

**Single-replica by design.** The Tier-2 RPS baseline is only correct when one consumer sees the whole stream; scaling `alert-cg` to N consumers would shard events and make every per-endpoint RPS an undercount. So the alert consumer runs as one replica — unlike the analytics consumer, whose idempotent counting is safe to parallelise. (`rps_window.py` documents this; the compose service pins `CONSUMER_NAME=alert-1`.)

**Deviation from the guide — the guide's `worker.py` is pseudocode, not runnable (revision #11):** it imports `from app.dedup`/`from app.rules` (wrong package — these live in `alerts/`) and references `get_redis()`, `ensure_consumer_group()`, `parse_event()`, `CONSUMER_NAME`, and a `RollingRpsCounter` that are never defined; its final `xack` builds the id list with a broken nested comprehension. Rebuilt on the *analytics* `worker.py` template: real `ensure_consumer_group` (id=0/mkstream), stable consumer name, poison-tolerant parse.

**Verified live (all Week 8 done-when checks):**
- SQLi (`q=' OR 1=1--`) and double-URL-encoded path traversal (`%252e%252e%252f…`) each → correctly structured `THREAT_DETECTED` alert within ~ms of the request.
- 50-probe burst → exactly one published alert (dedup collapse), 49 suppressed.
- Sustained spike past a warmed baseline → `BEHAVIORAL_ANOMALY`; no alert during the cold-start window.
- `alert-cg` progress fully independent of `analytics-cg` (stop one, the other keeps draining).
- Alert JSON shape confirmed over `SUBSCRIBE dashboard:alerts` before the dashboard exists to consume it.
- 27 new unit tests (rules incl. benign "select" false-positive + ReDoS-timing case; cold-start/spike/eviction; dedup burst+expiry+sweep; RPS bucketing) — 35 consumer tests pass in total.

---

## Tooling — Playwright API E2E suite (`e2e/`)

**Why a second test suite alongside `gateway/tests` (pytest).** The pytest suite calls the FastAPI app in-process via `httpx.ASGITransport` — fast, and sufficient for app logic, but it never actually crosses a socket. `e2e/` is a deliberately different kind of test: a black-box client hitting the *deployed* compose stack over real HTTP, exercising the real proxy, real Redis-backed auth cache and rate limiter, and real uvicorn response headers. No browser is launched — Playwright's `request` fixture does pure API testing, so there's no `playwright install` browser download; that changes once the Week 9 dashboard exists and gets real browser specs.

**Fixture design — two fixed tenants, seeded idempotently.** `tenants.name` has no unique constraint (only `api_key_hash` does), so `global-setup.ts` seeds by **delete-then-insert** on every run rather than upserting. That turned out to matter for more than idempotency: `tenant_id` is `gen_random_uuid()`-default, and the Week 4 limiter's key is `rate:{tenant_id}:route` — so a fresh `tenant_id` on every run also resets the sliding-window budget, which is what lets `rate_limit.spec.ts` assert an exact request count deterministically instead of inheriting whatever a previous run already spent.

That surfaced a real correctness gap during setup, not just a theoretical one: `app/auth.py` caches the tenant lookup in Redis for 30s, keyed by `api_key_hash` — and the raw API key here is intentionally fixed across runs (so specs don't need coordination), meaning the hash is fixed too. Reseeding a new `tenant_id` behind an unchanged, still-cached hash would silently serve the *old* tenant_id back to the gateway for up to 30s. Confirmed live: rerunning the suite immediately (well inside the 30s TTL) without a cache-eviction step reused the stale cached tenant. Fixed by having `global-setup.ts` `DEL` the two `tenant:apikey:<hash>` keys in Redis right after reseeding Postgres — verified by rerunning twice back-to-back, both times green, both times the rate-limit spec tripping 429 exactly at its own tenant's limit rather than an inherited one.

A separate `e2e-ratelimit` tenant (rate_limit_rps=3) exists solely so that spec can trip 429 in a handful of requests, rather than needing ~100 requests against the shared `e2e-auth` tenant's 100 rps policy (which would also pollute that tenant's window for the auth/proxy specs running nearby).

**Coverage (6 specs, all live-verified against the running stack):** missing-header 422 vs invalid-key 401 vs valid-key 200 (`auth.spec.ts`); upstream passthrough with query-param preservation and `X-API-Key` stripped before forwarding (`proxy.spec.ts`); exceeding a tight policy → 429 with `Retry-After` and `X-RateLimit-Remaining: 0` (`rate_limit.spec.ts`).

---

## Phase 5b (Week 9) — Hot-reload policy engine & real-time dashboard

**Context.** Week 5's policy cache uses a flat 10s TTL — fine for routine changes, too slow for an operator tightening a rate limit on a tenant mid-attack. Part A layers Redis Pub/Sub (`policy:invalidate`) on top of that TTL as a *latency optimization only*: the TTL never goes away, so a lost or delayed Pub/Sub message degrades to "stale for up to 10s," never to permanent staleness. Part B gives the alert consumer's `dashboard:alerts` output (Week 8, previously write-only — verified only via `redis-cli SUBSCRIBE`) an actual first consumer: a `/ws/dashboard` WebSocket fan-out on the gateway, plus a per-second `dashboard:metrics` RPS snapshot (new this week, published by the same single-replica alert consumer — reusing its existing single-replica guarantee rather than building leader election just to own one more channel). Part C is the Next.js dashboard itself, the actual operator-facing deliverable.

### Revision #8 — guide's cache-invalidation listener reconstructs a cache key that never exists

The guide's `listen_for_invalidations` rebuilds the key to delete as `policy:{tenant_id}:{route_pattern}` (e.g. `policy:<id>:/proxy/*`). But `app/policy.py`'s cache is keyed by the *literal request path* (`policy:<id>:/proxy/get`), never the glob pattern — so the guide's own invalidation code would silently no-op forever, leaving every cache entry to expire only via the Week 5 TTL regardless. Fixed in `gateway/app/policy_invalidation.py` by `SCAN`-ing and deleting every `policy:{tenant_id}:*` entry for the tenant, which doesn't need to know which literal paths are cached.

### Revision #13 — guide's admin PATCH has no tenant scoping (cross-tenant IDOR)

The guide's policy-update pseudocode is `UPDATE policies SET ... WHERE id = :id AND policy_version = :expected_version` — no `tenant_id` anywhere in the `WHERE` clause. Since policy IDs aren't secret and every tenant hits the same endpoint with only their own API key as auth, this lets any authenticated tenant patch *any other tenant's* policy by guessing/enumerating IDs. `gateway/app/routes/admin.py` adds `AND tenant_id = :tenant_id` to the update itself, and the no-row-returned fallback path re-checks existence scoped by `tenant_id` too — so a cross-tenant `policy_id` returns a plain 404, never leaking that the ID exists at all (verified live: patching `e2e-ratelimit`'s policy ID using `e2e-auth`'s key → 404, not 403 or 200).

### Local environment quirks discovered this week

- **Redis pub/sub needs an untimed connection, separate from the hot-path client.** The existing `_redis` client (`redis_client.py`) is deliberately tuned with aggressive `0.2s` socket timeouts for hot-path fail-open behavior — reused naively for `pubsub.listen()`, that produces a reconnect-storm, since `listen()`'s long blocking read looks identical to a timed-out dead connection. Fixed with a second, untimed `_pubsub_redis` client used only by `policy_invalidation.py` and `dashboard_ws.py`'s `fanout_loop`.
- **SELinux's `:Z` bind-mount flag is exclusive, not shared — a second reader gets locked out, silently.** Already known from Phase 0/1 for a *single*-container mount; this week it recurred in two new ways. (1) Horizontally scaling the gateway (`--scale gateway=2`, done to verify multi-replica WS fan-out) failed the second replica with `Could not import module "app.main"` — both containers bind-mounted `./gateway/app` with `:Z`, which grants exclusive relabeled access to exactly one container. (2) The exact same bug was latent, unnoticed, between `analytics-consumer` and `alert-consumer` — both have always bind-mounted `./consumers` with `:Z`, and it happened to not manifest until a full stack `docker compose down && up` produced a real cold-start race, at which point `alert-consumer` failed with `ModuleNotFoundError: No module named 'alerts'`. Both fixed by changing to `:z` (shared relabel) in `docker-compose.yml` — SELinux enforcement stays on, just not exclusive to one container.
- **Docker's port-range allocator uses a forward-only cursor, not "lowest free port."** After the `--scale gateway=2` test above (which needed `ports: ["8000-8005:8000"]` to give each replica a distinct host port), scaling back down to one replica did *not* rebind it to 8000 — confirmed the OS-level socket for 8000 was genuinely free (a raw Python `bind()` succeeded) on every attempt, yet repeated `stop/rm/up` cycles kept climbing: 8001, then 8002. The range mapping was reverted to a fixed `ports: ["8000:8000"]` once the one-off scaling verification was done; every other command in this repo assumes `localhost:8000`; if scaling needs live-testing again, widen the range temporarily.
- **Chrome's Local Network Access (LNA) checks block a page from opening a WebSocket to `localhost` by default.** First hit running the original raw-WS Playwright harness (`net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`), diagnosed via Playwright's `page.on('websocket', ...)` instrumentation (the browser's own `WebSocket.onerror` is deliberately vague per spec and gave no detail). Disabled for the test harness via Chromium launch args (`--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks`, `e2e/playwright.config.ts`). **Not just a test-harness quirk**: the real dashboard (its own origin) connecting to the gateway's `ws://` endpoint is the identical cross-origin, local-network pattern — relevant again if the local dev dashboard (`localhost:3000`) is ever pointed at a non-localhost gateway, or in any future non-TLS/non-same-origin deployment.

### Dashboard (`dashboard/`, Part C)

Next.js (App Router, TypeScript, Tailwind — scaffolded via `create-next-app`, which installed the current 16.x rather than the guide's-era 14.x; the App Router/`"use client"` patterns used here are unchanged across that range) + Recharts + a native browser `WebSocket` (no client library, per the guide's own spec). One page: a live RPS line chart fed by `METRIC_SNAPSHOT` frames (capped at 60 points) and a threat feed fed by `THREAT_DETECTED`/`BEHAVIORAL_ANOMALY` (capped at 50, HIGH vs MEDIUM severity visually distinguished). `useDashboardSocket` (`lib/useDashboardSocket.ts`) owns the WebSocket lifecycle in a single `useEffect`: exponential backoff reconnect (1s → 30s cap, reset to 1s on a successful `onopen`), and a `cancelled` flag plus explicit `socket.close()`/`clearTimeout()` in the cleanup function so an unmount (or a `url` change) can never leave an orphaned socket still scheduling reconnects into unmounted state.

### Verified live (all Week 9 done-when checks)

- **Hot-reload latency:** dropping `e2e-auth`'s policy from 100→2 rps via `PATCH /admin/policies/{id}` enforced the new limit within ~0.1s (6 requests, 429 tripped at request 3, 0.126s total) — vs. the old 10s TTL-only path.
- **Optimistic locking:** stale `expected_version` → 409; cross-tenant `policy_id` → 404 (see Revision #13).
- **TTL backstop for a genuinely missed Pub/Sub message:** simulated by updating a policy directly via SQL (bypassing the admin API and therefore the `publish` entirely) — confirmed the gateway kept enforcing the *old* cached limit for several seconds afterward (5/5 requests succeeded), then, without any further signal, automatically picked up the new limit once the 10s TTL expired (5/5 requests hit 429). This is the actual failure mode Part A's design is supposed to survive, not just the happy path.
- **Multi-replica fan-out:** with the gateway scaled to 2 replicas on separate ports, an SQLi request routed through replica A produced an identical `THREAT_DETECTED` payload on a dashboard WebSocket client connected to replica B — confirming the Redis Pub/Sub bridge, not any in-process state, is what makes fan-out work across independent gateway processes.
- **SQLi → dashboard alert:** an injected `q=' OR 1=1--` request produces a HIGH-severity entry in the dashboard's threat feed within ~2s.
- **Gateway restart → dashboard auto-reconnects:** with the dashboard connected, restarting the `gateway` container flips the UI to "reconnecting" and back to "live" on its own — no page reload — via the backoff-reconnect logic above.
- 9 Playwright specs pass end-to-end against the full compose stack (6 API-only + 3 real-browser specs against the actual dashboard UI, `e2e/tests/dashboard_ws.spec.ts` — these replaced an earlier raw-WebSocket-harness version of the same file, per that file's own forward-pointing comment, now that a real UI exists to drive).

---

## Phase 5c (Week 10) — Observability: Prometheus, cross-process tracing, structured logging, Grafana

**Context.** Three previously-invisible things become visible this week: request-level metrics (`prometheus-client`, already a Week-0 dependency but unused until now), a request's journey across the async gateway→Redis Streams→analytics-consumer boundary (W3C `traceparent` propagation), and per-request log correlation (`structlog` + `contextvars`, likewise a long-installed but unused dependency). Grafana/Prometheus are added to the compose stack as the first consumers of all three.

**Label cardinality discipline (`gateway/app/metrics.py`).** Every label on every metric here is a small, bounded set: `status_class` (`"2xx"/"4xx"/"5xx"`, computed from the status code, never the raw code itself) is the only label used anywhere. Deliberately *not* labeled: `endpoint` (`/proxy/{path:path}` is user-controlled and unbounded — a labeled metric per distinct path would grow a new Prometheus time series forever) and `tenant_id` (same problem, worse — a new tenant is a permanent new series). This is the practical failure mode label-cardinality guidance warns about: Prometheus has no way to expire a label combination that stops appearing, so an unbounded label is a slow, silent memory leak in the metrics backend itself, not just noise.

**Two histograms, two different bucket scales, because they measure different things.** `shieldstream_proxy_latency_ms` (end-to-end, buckets 1–1000ms) and `shieldstream_redis_lua_latency_ms` (the `evalsha` call alone, buckets 0.1–50ms). Prometheus's default buckets (5ms–10s) would put the *entire* Lua-latency distribution inside a single bucket, making the histogram useless for exactly the sub-2ms precision the Week 4 sliding-window script was designed to hit (see Phase 3a).

**Cross-process tracing (`gateway/app/tracing.py`'s `current_traceparent()`, `gateway/app/events.py`'s new `traceparent` field, `consumers/analytics/tracing.py`).** The gateway injects the current span's W3C traceparent into the Redis Streams event at emit time (`opentelemetry.propagate.inject`); the analytics consumer extracts it per-event (`opentelemetry.propagate.extract`) and opens a short `analytics.ingest_event` span parented to that extracted context, rather than starting an unrelated new trace. The span deliberately does *not* wrap the actual batched DB write (which spans many events at once, from different original traces) — its only job is to put a second, correctly-parented span onto each request's trace. Scoped to the main consume loop only (not `recover_own_pending`/`adopt_orphaned_pending`'s backlog-recovery paths) — a deliberate simplification, since backlog/orphan recovery isn't the steady-state path this feature is verifying.

**Structured logging (`gateway/app/logging_config.py`, `gateway/app/middleware/request_id.py`).** A `RequestIdMiddleware` (real Starlette middleware, not a dependency) generates one UUID per request, binds it via `structlog.contextvars.bind_contextvars`, and clears it in a `finally` — safe here in a way the Week 5 rate limiter's own middleware attempt wasn't (REVISION #3): this module never touches `request.state.tenant`. `structlog.stdlib.ProcessorFormatter` with a shared processor chain (`merge_contextvars`, `ExtraAdder`) means *every* log line — new `structlog.get_logger()` calls **and** every pre-existing plain `logging.getLogger(...)` call from Weeks 0–9 — gets the same JSON rendering and the same `request_id` merged in automatically, with zero changes required to that old code.

**Grafana/Prometheus (`docker-compose.yml`, `observability/`).** Provisioned entirely by file (datasource, dashboard JSON, alert rule) — no click-ops, so the whole observability stack reproduces from a fresh `docker compose up`. Dashboard: RPS by status class, proxy p50/p99 (`histogram_quantile` over the new histogram), analytics consumer lag (a new `Gauge` in `consumers/analytics/worker.py`, updated from `XPENDING`'s summary form on the same 15s cadence as the existing orphan sweep — reusing an existing timer rather than adding a new one), and rate-limit rejection rate.

**Bug found and fixed live — Grafana's threshold expression rejects a raw range-query result.** The alert rule's first version fed the `histogram_quantile(...)` query directly into a `type: threshold` expression (`condition: C`, `expression: A`). Every evaluation failed with `invalid format of evaluation results for the alert definition C: looks like time series data, only reduced data can be alerted on.` — Grafana's threshold node needs a single reduced number, not an instant-vector time series, even when that vector happens to carry only one sample (as a label-free `histogram_quantile` result does here). Fixed by inserting a `type: reduce` expression (`B`, `reducer: last`) between the query and the threshold, and reloading via `POST /api/admin/provisioning/alerting/reload` rather than a full container restart. This is the same class of bug this project has repeatedly found in the *guide's* pseudocode — except this one was self-inflicted, in hand-written provisioning YAML, and caught the same way: by actually running it and reading the real error, not by code review.

**Verified live (all Week 10 done-when checks):**
- `/metrics` shows correctly typed output (`# TYPE` lines match Counter/Histogram semantics) and is load-responsive: `shieldstream_requests_total{status_class="2xx"}` and `shieldstream_proxy_latency_ms_count` both moved from absent/zero to accurate counts after generating traffic.
- **Single Jaeger trace spans both processes**, confirmed via the Jaeger query API, not just the UI: a single `traceID` contains `GET /proxy/{path:path}` → `auth.validate_key` → `rate_limiter.check` → `proxy.forward` (all `shieldstream.gateway`) and `analytics.ingest_event` (`shieldstream.analytics-consumer`) as one continuous timeline.
- **Logs correlate via request_id:** a single proxied request's `httpx`/`httpcore` debug lines and the new `proxy_request_completed` line all share one `request_id` value, confirmed by grepping live container logs for a specific `X-Request-Id` response header value.
- Grafana dashboard provisioned and confirmed live (not just "no errors on startup"): queried the RPS panel's exact Prometheus expression through Grafana's own datasource proxy before and after generating traffic, and the value moved from `0` to a real nonzero rate.
- **Alert rule full lifecycle, timed against real wall-clock timestamps, not just log messages:** entered `Pending` at `02:15:20Z` under sustained induced latency (`/proxy/delay/1` looped against httpbin), reached `Firing` at exactly `02:17:20Z` — precisely the configured `for: 2m` — then, after the induced load was stopped and traffic returned to its ~5ms baseline, resolved to `Normal` at `02:19:20Z`. All three states confirmed via the alert-rules API, not inferred.
- Cardinality reviewed (see above) — no unbounded label anywhere in the new metrics.

---

*(Phase 5 continues: Week 11 load testing + deployment.)*

---

## Phase 6a (Week 11) — Load testing: three real bottlenecks found and fixed live

**Context.** Locust, a custom `LoadTestShape` (ramp 0→1000 users/60s, hold 5min, ramp down/60s — `loadtest/locustfile.py`), weighted mixed traffic (70% GET, 25% POST, 5% a low-frequency SQLi probe keeping the alert path exercised under load without dominating the latency distribution the p50/p99 targets describe). Runs as its own compose service (`--profile loadtest`, not part of default `up`) against a dedicated `loadtest` tenant (`loadtest/seed_tenant.sql`) — a fixed API key and a generous policy, so the test measures proxy overhead, not the rate limiter's own 429 path.

**Deviation from the guide, acknowledged honestly:** the guide's own advice is to run the load generator on a separate *machine* from the target, arguing generator/target contention corrupts the measurement. This environment has one machine. A separate container is what's actually implemented — real isolation from the gateway's own process, but not real isolation from the gateway's own CPU core. This matters for how the final numbers below should be read.

**Three bottlenecks found live, in the order the failures pointed to them — none anticipated, none guessable from code review, all found by actually running the test and reading the real error:**

1. **httpx connection pool (`gateway/app/http_client.py`): `max_connections=100`.** Fine for every prior week's traffic (never more than a few dozen concurrent requests); at 1000 concurrent users, ~900 requests queued for a pool slot and blew `routes/proxy.py`'s 2s pool timeout. First full run: **63.7% failure rate**, ~13s average latency. Raised to `max_connections=1200` (with `max_keepalive_connections` 20→200) — headroom past the load test's own peak concurrency.
2. **SQLAlchemy engine pool (`gateway/app/db.py`): `pool_size=10, max_overflow=5`.** `app/auth.py`'s 30s auth-cache TTL means a burst of *new* connections (a load test's ramp-up, or a real flash-crowd) arriving faster than the cache warms all fall through to the DB simultaneously — a genuine thundering herd on cache-miss, not a hypothetical one. Confirmed via `sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 5 reached` in the gateway's own logs, causing 500s. Postgres's own `max_connections` is 100 (`SHOW max_connections`); raised the pool to 30/20 (50 total), leaving headroom against the admin engine's 4 and the analytics consumer's 5.
3. **Container file descriptor limit: default `ulimit -n 1024`.** 1000 concurrent client sockets plus this process's own upstream and DB/Redis connections exceeded it — `OSError(24, 'Too many open files')`, logged by `asyncio` itself (`socket.accept() out of system resource`) rather than raised as a normal FastAPI exception, so it never surfaced as a clean 500 the way the other two did. Raised via `ulimits: nofile: 65536` on the `gateway` service in `docker-compose.yml`.

**A fourth issue, self-inflicted in the load-test tooling rather than the gateway:** the first `loadtest` tenant seed set `rate_limit_rps=5000, rate_limit_window_s=60`, misreading the schema — per the Lua script's own comment (`gateway/app/lua/sliding_window.lua`), `limit` is *"max requests allowed inside the window,"* not a true per-second rate. `5000/60s` is an effective ~83 req/s sustained cap, not 5000 req/s, and real 429s showed up mixed into the connection-pool failures the test was meant to isolate. Fixed by setting `window_s=1`, making the field's name and its actual behavior agree.

**Final result, honestly reported (not the guide's target, and said so directly):**
- **Error rate: 0.005%** (2 failures out of 47,311 requests) — comfortably under the guide's `<0.1%` target, and the real payoff of the three fixes above (baseline before fixes: 30–64% failures, depending on which bottleneck dominated at the time).
- **p50: 4.2s, p99: 33s** — far over the guide's 15ms/50ms targets. Root cause confirmed via `docker stats`: the gateway container's CPU sat at ~100% (one core, fully saturated) for the entire hold phase, and response time climbed monotonically throughout the 5-minute hold (median went from ~350ms at ramp-up to over 4s by the end) — the textbook signature of sustained overload, where arrival rate exceeds one process's service rate and the queue never drains. This is a single-core capacity ceiling specific to this project's current deployment shape (one `uvicorn` worker, one container, on this sandbox's hardware) — not evidence of a per-request inefficiency in the app itself (Phase 2 already measured that in isolation: ~6ms proxy overhead at a concurrency the downstream could actually sustain). The real fix is horizontal scaling, which Week 9 already proved works (`--scale gateway=N` + Redis Pub/Sub fan-out, live-verified across replicas) — not something to chase further inside this single-container benchmark.
- `loadtest-results/` (HTML report + CSVs) committed as the done-when evidence, bottleneck iteration and all — an honest number, in the same spirit as Phase 2's own httpbin-concurrency finding, not a number engineered to look good.

---

## Phase 6b (Week 11) — Hybrid free-tier deployment: formalizing a prior decision, artifacts prepared not provisioned

**Context.** The guide's own Week 11 deployment section — and the Blueprint's Section 8 — is 100% AWS-native: CloudFormation, ECS Fargate, RDS, ElastiCache, ALB, ~$65/month. A prior session decided against that in favor of a ~$0/month hybrid path (one always-free VM + Docker Compose, Neon Postgres, Vercel for the dashboard, Caddy instead of an ALB) — but that decision had only ever been recorded in a passing memory note, never actually written into this document. This entry is that formalization, plus the prepared (not provisioned) artifacts: `infra/docker-compose.prod.yml`, `infra/Caddyfile`, `infra/neon-notes.md`, `infra/DEPLOY.md`, `infra/.env.prod.example`.

**No cloud accounts exist in the environment these artifacts were written in** — nothing here has been run against a real VM, a real Neon branch, or a real Vercel project. Treat `infra/DEPLOY.md` as an unexecuted runbook, not a record of a completed deployment.

**One assumption from that prior decision turned out to be wrong, caught by actually checking instead of carrying the assumption forward:** the memory note flagged "verify TimescaleDB continuous aggregate support on Neon" as an open risk. Checked against Neon's own extension documentation — **`timescaledb` is supported on Neon** (Apache-2 licensed tier, all current Postgres versions), and this project's schema only ever uses that tier (`create_hypertable`, no compression, no continuous aggregates — the continuous aggregate was already dropped in Phase 1's **Revision #6** for an unrelated RLS conflict, and is separately a Timescale-licensed, not Apache-2, feature anyway). Net effect: the Neon migration path needs no schema changes, a better outcome than the open risk implied — see `infra/neon-notes.md` for the full finding and the one real migration step it does require (generating real secrets for the two `CREATE ROLE ... PASSWORD` literals before running against Neon, since the migration already creates both `shieldstream_app`/`shieldstream_worker` roles itself, idempotently).

**Caddy replaces the ALB** for the same reason Redis Streams replaced Kafka and SHA-256 replaced bcrypt elsewhere in this project: it does the one thing actually needed (automatic HTTPS, reverse proxy, WebSocket upgrade passthrough) without the AWS-specific machinery the guide's version assumes. Grafana/Prometheus/Jaeger stay self-hosted on the same VM (still free) rather than migrating to a managed observability SaaS — only Postgres (durability/backup story materially better managed) and the dashboard (zero-config static hosting) move off the VM.

