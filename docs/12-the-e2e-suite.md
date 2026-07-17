# Chapter 12 — A Second Test Suite, and the Bugs Only It Could Find

By this point the gateway had a full pytest suite, and it was genuinely
useful — fast, thorough on application logic, and run constantly during the
work in Chapters 6 through 11. It also had a structural blind spot, and the
blind spot is the entire reason a second test suite exists at all.

## What the fast suite can't see

The pytest suite calls the FastAPI application in-process, via `httpx`'s
ASGI transport — no real network socket, no real running server, just Python
calling Python through an adapter that mimics the ASGI protocol. That's a
deliberate, good trade for what it's testing: fast enough to run on every
change, and entirely sufficient for verifying application *logic*. What it
structurally cannot verify is anything that only exists once a request
crosses an actual socket — a header that gets added or dropped only by a real
HTTP response cycle, or, starting in Week 9, a WebSocket handshake, which has
no meaningful equivalent in an in-process ASGI call at all.

Playwright's `request` fixture fills that gap: a genuine black-box HTTP
client hitting the *deployed* Docker Compose stack, exercising the real
gateway process, the real Redis-backed auth cache and rate limiter, and real
`uvicorn` response headers — nothing mocked, nothing running in-process.
Through Week 8, this suite does pure API testing; no browser is launched at
all, so there's no browser-download step in the toolchain. That changes in
Chapter 14, once the dashboard exists and needs a real browser to drive it as
an actual WebSocket client.

## Fixture design, and a correctness bug the fixtures themselves surfaced

Two fixed test tenants are seeded before every run. `tenants.name` has no
uniqueness constraint in the schema — only `api_key_hash` does — so the setup
script seeds by deleting and re-inserting on every run, rather than
attempting an upsert that the schema doesn't actually support cleanly.

That choice turned out to matter for more than just idempotency. Tenant IDs
default to `gen_random_uuid()`, and the rate limiter's own Redis key (built
back in Chapter 7) is scoped by tenant ID: `rate:{tenant_id}:route`. A fresh
tenant ID on every run means a fresh, empty rate-limit budget on every run
too — which is exactly what lets the rate-limit spec assert an *exact*
request count deterministically, rather than inheriting whatever budget a
previous run happened to leave behind.

But a fresh tenant ID collides with something else that was already caching
by a *different* key. Authentication (Chapter 6) caches the tenant lookup in
Redis for thirty seconds, keyed by the API key's hash — and the raw API key
used in these fixtures is intentionally fixed across every run, so specs
don't need any runtime coordination to know what key to send. A fixed raw
key means a fixed hash, every time. Reseeding a brand-new tenant ID behind an
*unchanged, still-cached* hash would silently keep serving the *old* tenant
ID back to the gateway for up to thirty seconds after a fresh reseed — the
cache has no way to know the underlying tenant identity has changed, because
from its perspective the lookup key never did.

This wasn't a theoretical concern raised and then dismissed — it was
confirmed live. Rerunning the suite immediately, well inside the thirty-second
window, without an explicit cache-eviction step, reused the stale cached
tenant. The fix: the setup script now deletes both tenants' cache entries in
Redis immediately after reseeding Postgres, closing the window entirely
rather than working around it. Verified by running the full suite twice in
immediate succession — both runs green, and both times the rate-limit spec
correctly tripping its `429` at exactly *its own* tenant's limit, never at a
count inherited from the previous run's leftover cache state.

A second tenant, seeded with a deliberately tight rate limit, exists purely
so the rate-limit spec can trip its `429` in a small handful of requests
rather than needing on the order of a hundred requests against the shared
tenant's much higher default limit — which would also pollute that shared
tenant's rate-limit window for the authentication and proxy specs running
nearby in the same suite.

## What this suite covers, through Week 8

Six specs, every one of them run against the genuinely running compose
stack rather than an in-process substitute: a missing authentication header
correctly returns a `422` (framework-level validation, distinct from
application-level rejection) versus an invalid key returning `401` versus a
valid key succeeding with `200`; upstream passthrough correctly preserves
query parameters and strips the `X-API-Key` header before forwarding;
exceeding a tight policy returns `429` with both `Retry-After` and
`X-RateLimit-Remaining: 0` present and correct.

## Why this chapter sits where it does

Nothing in this suite tests a feature that hasn't already been covered by
this book's earlier chapters — its entire value is in testing those same
guarantees a second, structurally different way, over a real socket instead
of an in-process call. That's worth a chapter of its own rather than a
footnote on Chapter 6, because the gap it closes — real HTTP behavior a fast
in-process suite can't see — becomes load-bearing almost immediately: Chapter
14's dashboard specs, and much of the live verification narrated in every
chapter from here through the end of the book, run through this exact
harness.
