# Chapter 7 — An Atomic Rate Limiter, Proven Not Assumed

Rate limiting is one of the canonical systems-design interview questions for a
reason: the naive answers all fail in specific, well-known ways, and getting
it right requires actually understanding why each simpler approach breaks, not
just knowing the name of a better algorithm to reach for.

## Why the obvious approaches don't work

**Fixed-window counting** — `INCR` a counter, `EXPIRE` it after the window
elapses — is simple and has a real flaw: it allows a caller to send the full
limit's worth of requests in the last moment of one window, and then
immediately send the full limit again in the first moment of the next window.
From the caller's perspective, that's up to *twice* the intended limit inside
a short span straddling the boundary. The window resets sharply; traffic
doesn't.

**A sliding-window log implemented as separate Redis calls from application
code** — read the current count, decide whether to allow, then write the new
entry — looks like it fixes the boundary problem, and it introduces a
different one: a race condition. Two concurrent requests can both read "9 of
a limit of 10, proceed" before either has recorded its own request, because
Redis's `MULTI`/`EXEC` guarantees that queued commands within *one*
transaction execute atomically, but it says nothing about isolation *between*
two separate transactions' read and write phases. Two clients racing through
"check, then act" as separate steps can both act as if they were first.

**Token bucket** was seriously considered — it's a genuinely strong
algorithm, and it allows controlled bursting in a way a strict window doesn't.
It was set aside for two reasons specific to this project: its state is a
single opaque number (the current token count), not a replayable log, so a
tenant asking "why exactly was I rate limited just now" has no precise answer
available from the state alone; and a correct distributed implementation adds
refill-timing complexity that wasn't justified for what this project actually
needed.

**`WATCH`-based optimistic locking** with client-side retry on conflict —
works, but trades the race condition for retry-storm risk under contention,
and pushes real complexity into every caller instead of containing it in one
place.

## The decision: one atomic script, not a careful sequence of calls

The chosen design is a sliding-window log stored as a Redis Sorted Set — one
member per request (a UUID), scored by its timestamp in milliseconds — with
the entire check-then-act sequence expressed as a single Lua script:
`ZREMRANGEBYSCORE` to discard entries older than the current window, `ZCARD`
to count what's left, and a conditional `ZADD` plus `PEXPIRE` if the count is
under the limit.

The reason this eliminates the race isn't careful call ordering — it's that
Redis executes a Lua script to completion as one atomic unit. No other
client's command, not even another Lua script, can interleave partway
through. The race is closed *by construction*, not by discipline about how
the calls happen to be sequenced.

On the hot path, the script is invoked via `EVALSHA` — sending only the
script's hash, not its full body, on every call — with a fallback that
catches `NoScriptError` (Redis's script cache got flushed, or Redis itself
restarted, while the application still holds a now-stale hash), reloads the
script, and retries exactly once.

## What this costs

The Sorted Set stores one entry per request currently inside the window — a
real, unbounded-with-traffic memory cost, at roughly 64 bytes per entry. At
high enough per-key request volume and cardinality (many distinct rate-limit
keys, each under heavy load), this stops being the cheap option. That's a
known, documented limitation, not an oversight — the migration path if it
ever mattered would be approximate counting (HyperLogLog) or a move to token
bucket for cases where cardinality is bounded and exact request-level replay
isn't actually needed. At this project's scale, it was never close to being
the limiting factor.

## Proving it, not trusting it

The whole point of choosing "atomic Lua script" over "careful sequence of
calls" was to make a specific claim: under real concurrency, this cannot
over-admit. That claim was tested, not just argued for.

A hundred concurrent async calls fired at once against a limit of ten — via
`asyncio.gather()`, genuinely concurrent, not a sequential loop dressed up to
look concurrent — produced exactly ten allowed and ninety blocked. Sequential
calls could never have exposed a race even if one existed; concurrent
dispatch is what makes the test actually test the property it claims to test.
That same concurrency proof was run fifty times in a row via `pytest-repeat`:
fifty passes out of fifty, no flakiness — because a race condition that only
shows up occasionally under scheduling luck is exactly the kind of bug a
single passing run can hide.

Two more properties were checked separately: the sliding window genuinely
resets once time moves past it (verified with a time-mocked test — the exact
scenario a fixed-window counter gets wrong at the boundary), and live,
non-mocked Redis round-trip latency for the script call came in at 0.116ms
p50, 0.206ms p99 — comfortably inside the target of under 1ms and 2ms
respectively.

One tooling detail worth naming: the unit tests run against `fakeredis[lua]`,
which executes the *actual* Lua script through a real embedded Lua
interpreter (via the `lupa` binding), not a Python reimplementation of what
the script is supposed to do. That distinction matters — a test suite that
validates behavior against a hand-rolled reimplementation of the script's
semantics is really just testing that the reimplementation matches the
script's *author's* understanding of the script, which is exactly the kind of
test that would pass even if the actual atomicity guarantee were broken. This
one runs the real script.

## What's deliberately not done yet

By the end of this chapter, `check_rate_limit()` exists, is proven correct in
isolation, and nothing calls it. There's no middleware, no policy-driven
per-tenant limits, no `429`/`Retry-After` response shape, no fail-open
behavior if Redis itself becomes unreachable. That's not an oversight — it's
a deliberate boundary between "prove the algorithm is correct" and "wire it
into a real request path with all the failure modes that implies," and the
second half of that is Chapter 8, which turns out to contain most of the real
surprises.
