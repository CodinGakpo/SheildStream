# Chapter 6 — The Reverse Proxy and Two Bugs Load Testing Found

With authentication and tenant isolation in place, Week 3 built the thing
ShieldStream actually is at its core: a reverse proxy. A request arrives,
gets authenticated against the tenant's API key, gets forwarded to that
tenant's own upstream service, and the response comes back with the
credential-bearing header stripped before it ever reaches the caller's own
backend — the gateway's API key authenticates the caller *to ShieldStream*,
and the downstream service has no use for it, so forwarding it unmodified
would just widen the credential's exposure surface for no benefit.

None of that is where the interesting engineering happened. The interesting
part started once the proxy worked correctly and someone asked, reasonably,
"how much latency does this actually add?" — and then didn't stop at the
first number that came back.

## A dependency that ran even when nothing needed it

FastAPI resolves every parameter declared with `Depends()` before the route
handler's body runs at all — regardless of whether the body's own control
flow ever actually uses that dependency. The tenant-lookup function had
originally declared `db: AsyncSession = Depends(get_db_session)`, following
the guide's own pattern. That means *every single request* acquired a pooled
Postgres connection — even the overwhelming majority of requests that hit a
warm Redis cache and never touch the database at all.

This didn't show up as a functional bug. It showed up as a latency curve that
got steadily worse under concurrency for no visible reason: 2.6ms p50 at
concurrency 1, climbing to roughly 47ms p50 at concurrency 20 on the proxy
endpoint — while the database-free `/health` endpoint stayed flat at 5.9ms
across the same concurrency range. Isolating the comparison to a route that
provably never touches the database and watching it stay fast is what pinned
the cost specifically to the authentication path, not to something more
general like connection-pool contention across the whole app.

The fix removed the `Depends()` declaration entirely; the authentication
function now calls `session_factory()` directly, and only inside the branch
that actually handles a cache miss. `get_db_session` stays available as a
dependency for any route that genuinely needs a session unconditionally — the
fix isn't "never use this dependency," it's "don't declare it where the
control flow might not need it." There's no trade-off to report here: this is
a strict improvement with no downside, and the guide's own reference code has
the identical latent bug — it was never caught because the guide's own testing
section never runs a concurrent benchmark against a cache-warm gateway. A
single-request functional test can't see a bug that only costs something
under concurrent load.

## The downstream that couldn't keep up with its own test

After that fix, proxy latency at concurrency 20 was still around 37ms p50 —
better, but not obviously *good*, and worth a second look rather than
accepting the number at face value. The investigation: hit the downstream
test service (`kennethreitz/httpbin`, the guide's own choice of stand-in
service for this week) directly with `httpx`, concurrently, entirely bypassing
ShieldStream. Two hundred concurrent requests straight to `httpbin` gave a
p50 of roughly 200ms.

That's not a ShieldStream number at all — it's `httpbin` itself. The image
runs a single or few-worker gunicorn development server, and it serializes
concurrent requests on its own, independent of anything the gateway does.
The guide names this exact image as its downstream test double for this week
without ever flagging that limitation, which means anyone following the guide
literally and benchmarking at meaningful concurrency would be measuring
`httpbin`'s own bottleneck and attributing it to the gateway.

The fix for *this* week's purposes wasn't to replace `httpbin` — that's a real
downstream test-target problem, correctly deferred to the load-testing phase
in Chapter 16, which does need a target that can actually sustain real
concurrency. For Week 3's narrower goal — proving the *gateway's own* overhead
is small — the answer was to measure the *delta* between a proxied and a
direct request at a concurrency level `httpbin` itself can actually sustain
without becoming the dominant cost, empirically around 5 concurrent requests.

At that concurrency: proxied p50 = 8.4ms, direct p50 = 2.3ms — a delta of
roughly 6.1ms, inside the guide's own stated target of under 8ms. The p99
delta came in slightly over target, closer to 10ms. Both numbers are reported
as measured, including the one that missed. This is explicitly *not* the
number to carry forward into the real load-testing work — it's flagged in the
decision log at the time as a number that will need `httpbin` swapped out for
something that can actually take load, precisely so it doesn't get forgotten
and mistaken later for a validated production benchmark. (It resurfaces
exactly this way in Chapter 18's chaos test, where `httpbin`'s concurrency
ceiling shows up again, years — well, weeks — after this chapter first
flagged it.)

## What was actually verified

By the end of Week 3: a proxied request with a valid API key forwards to the
tenant's upstream and returns the response unmodified, with the `Host` header
correctly reflecting the downstream's own value rather than leaking
ShieldStream's; an invalid key returns `401` before any downstream call is
even attempted; the `X-Api-Key` header is stripped before forwarding — a
tightening beyond what the guide itself does, verified by confirming the
downstream genuinely never receives it; the Redis auth cache populates
correctly on first request with a 30-second TTL; and a single distributed
trace, visible end-to-end in Jaeger, correctly nests the authentication span
and the proxy-forward span as children of one root span — with a 401 response
correctly showing *only* the authentication span, no forward child, because
the request never got that far.

Two threads carry forward from this chapter: the eager-dependency pattern
(fixed here, but the general shape — "don't make a request pay for work its
own logic doesn't need" — recurs through the project) and the `httpbin`
ceiling, deferred but not forgotten, waiting for Chapter 16 and making one
more unplanned appearance in Chapter 18.
