# Chapter 8 — Wiring the Limiter In, and What Happens When Redis Dies

Chapter 7 proved the rate-limiting algorithm was correct in isolation. This
chapter is about everything that goes wrong the moment "correct in isolation"
has to become "correct inside a real request path, talking to a real Redis
that can and does go down." Almost none of what follows was visible from
reading the code. All of it was found by actually breaking things on purpose.

## A middleware that would have crashed on its first request

The implementation guide wires rate limiting in as Starlette
`@app.middleware("http")`, and that middleware reads `request.state.tenant`
to know which tenant's limit applies. The problem: Starlette's middleware
chain runs *before* FastAPI resolves a route's own `Depends()` parameters.
`request.state.tenant` doesn't exist yet at the point the guide's middleware
tries to read it — the guide's own code, run as written, raises
`AttributeError` on the very first request that reaches it.

The fix was to not use middleware at all for this, despite the module still
living in a directory named `middleware/` for structural continuity with the
guide (documented plainly in the module's own docstring as a deliberate
naming choice, not an accident). Rate limiting is implemented as a FastAPI
dependency instead — `enforce_rate_limit`, which itself declares
`tenant: dict = Depends(get_tenant)` as a sub-dependency. FastAPI resolves
`get_tenant` once, then `enforce_rate_limit`, in an explicit order driven by
the dependency graph — not by hoping route registration order lines up with
an implicit assumption about execution order, which is exactly the kind of
thing the guide's own pitfall notes for this week warn is easy to get
backwards.

## A policy engine that has to survive its own cache going away

Rate limits are per-tenant, per-route, and configurable — stored in Postgres,
cached in Redis with a 10-second TTL (deliberately shorter than the
30-second tenant-identity cache from Chapter 6: an operator tightening a
limit on a tenant mid-incident wants that change to take effect fast, faster
than routine identity caching needs to be). Matching handles glob-style route
patterns with longest-pattern-wins tie-breaking, and the cached payload
carries a `policy_version` field from the very start — not because anything
needs it yet, but because the guide's own pitfall notes flag that adding
version tracking retroactively, after a cache schema is already in
production, is exactly the kind of migration nobody wants to do later. It
cost nothing to include now and avoided real friction in Week 9 (Chapter 13),
when it became load-bearing for a feature that didn't exist yet at the time
this cache was first designed.

Live-verified directly: dropping a tenant's limit from 100 to 2 requests via
a raw database `UPDATE`, then confirming enforcement caught up within the
10-second TTL bound.

## Fail-open, and the guide's own warning about how not to test it

The rate limiter's design intent is: if Redis becomes unreachable, requests
should keep flowing (fail open) rather than every request failing outright —
availability wins over strict enforcement during what's meant to be a brief
outage. The implementation guide's own pitfall notes for this week say
something specific and, it turned out, exactly correct: testing fail-open
behavior only by mocking the Redis client's exceptions, never by actually
killing the container, isn't sufficient. That warning was taken seriously —
the container was actually killed, live, mid-traffic — and it surfaced three
real defects that no mocked-exception unit test would ever have caught.

**One: authentication had no Redis error handling at all.** The tenant-lookup
cache read in `auth.py` was completely unguarded. A Redis outage 500'd
*every single request* before the rate limiter's own carefully-built
fail-open logic ever got a turn to run — because authentication runs first in
the dependency chain, and it was failing hard before execution ever reached
the code that was supposed to degrade gracefully. The fix follows the same
pattern already established for the policy cache: a `RedisError` on the cache
read falls through to Postgres — authentication's actual source of truth,
entirely independent of Redis — and a failure on the cache-*repopulation*
write afterward is swallowed rather than raised. This isn't "fail open" in
the security sense; authentication still genuinely checks the key against the
database. It's making the *cache layer* resilient enough that the rate
limiter's fail-open logic downstream ever gets a chance to execute at all.

**Two: no socket timeout on the Redis client, at all.** Against a container
that's fully stopped (not just DNS-broken, not just refusing new
connections — genuinely gone), a client with no configured timeout can hang
for tens of seconds on an OS-level TCP timeout before a `RedisError` ever
surfaces. A "fail-open" design that takes thirty-plus seconds per request to
actually trigger isn't preserving availability — it's replacing a clean,
fast failure with a slow, silent one, which from a caller's perspective is
often worse. Fixed with explicit, short `socket_connect_timeout=0.2` and
`socket_timeout=0.2` on the shared Redis client.

**Three: a genuine interaction bug between `uvloop` and async DNS
resolution** — not an application bug, but real, reproducible, and directly
caused by the fast-timeout fix above interacting with the async runtime.
With `uvloop` installed (uvicorn's default event loop when the
`uvicorn[standard]` extra is present), a timed-out Redis connection attempt
left the process's async DNS-resolution machinery in a state where the
*next, completely unrelated* async DNS lookup — `httpx` resolving the
downstream proxy target for the *next* request — also failed, consistently,
for the full configured connect timeout. This was captured precisely: a
Jaeger span showed the failure taking exactly 2002.1ms, matching the
configured `connect=2.0` budget to a tenth of a millisecond, which is what
made it clear this wasn't random flakiness but a deterministic interaction.
Isolated by reproducing it completely outside the application: a minimal
standalone script under the default asyncio loop never reproduced the issue;
the identical script with `uvloop.install()` reproduced it on every run. The
fix was blunt and deliberate — `uvicorn ... --loop asyncio`, giving up
`uvloop`'s throughput advantage specifically to get correctness during the
exact failure scenario this week's work exists to handle gracefully. For a
project whose actual point is provable correctness under failure rather than
maximum throughput, that's the right trade, made with eyes open rather than
discovered as a regression later. (The root cause wasn't traced to a specific
line inside `uvloop`'s or `anyio`'s DNS resolver — that would need bisecting
their source directly — but it was conclusively isolated to that layer via a
controlled, repeatable reproduction, which is the standard of evidence this
project holds itself to throughout.)

## What was actually verified, live, not mocked

After all three fixes: exceeding a seeded limit of 100 requests per 60
seconds allows exactly 100 through, and request 101 onward returns `429`
with `Retry-After: 60`, `X-RateLimit-Limit: 100`, and
`X-RateLimit-Remaining: 0` — with the remaining-count header present and
correctly decrementing on every successful response, not only on the
rejection. Killing the Redis container mid-traffic: every request still
returns `200` (fail-open genuinely engaging), bounded at roughly one second
total — the cost of three short, sequential 0.2-second cache-miss timeouts
stacking across authentication, policy, and the rate limiter itself, not a
30-second hang — with clean log lines for each fallback path taken.
Restarting Redis: distributed rate limiting resumes correctly with zero
manual intervention and no gateway restart required.

One more thing fixed in passing, discovered the same way as everything else
in this chapter — by actually running things rather than trusting they'd
work: `greenlet`, a required transitive dependency of SQLAlchemy's async
engine, silently failed to resolve in the local host virtual environment
despite being present inside the Docker image. It's now pinned explicitly in
`requirements.txt` instead of being relied on transitively — a small fix, but
the same underlying lesson as the rest of the chapter: don't trust that a
dependency graph resolves the way you expect just because it happened to work
somewhere once.
