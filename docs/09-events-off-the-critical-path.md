# Chapter 9 — Events Off the Critical Path

Every proxied request needs to feed two downstream systems that don't exist
yet at this point in the project: an analytics pipeline (Chapter 10) and a
threat-detection pipeline (Chapter 11). Both need to see, eventually, every
request the gateway handles. The design constraint for this week is strict
and stated plainly in the project's own goals: emitting that data has to be
*invisible* in the request-latency histogram — not "fast," invisible. A
security gateway that gets measurably slower every time it successfully
records what it just did has built its own bottleneck into its core loop.

## What doesn't work, and why

**A synchronous write to Postgres on every request** is the simplest thing
that could work, and it's wrong for a structural reason: it puts a second
storage system's latency directly on the client's critical path. Under load,
the database becomes a source of back-pressure on requests that have nothing
to do with the database at all — a proxied `GET` request now waits on a
write it doesn't need to have completed before it can return.

**A Redis List used as a queue** was considered and rejected for a more
specific reason: `LPOP` (or `RPOP`) is destructive — once one consumer reads
an entry, it's gone. This project needs *two* independent downstream readers
(analytics in Chapter 10, threat detection in Chapter 11), and a destructive
queue would need either duplicated writes at the producer (two lists, one per
consumer — coupling the producer to how many consumers currently exist) or a
fan-out layer built on top. Redis Streams, with consumer groups, give
publish-once/consume-independently as a native primitive — exactly the shape
this needs, with no extra machinery required.

**Awaiting the `XADD` inline**, even though it's fast (roughly 0.1ms in
isolation), was still rejected — it's a Redis round trip sitting directly in
the request's own execution path, and during any Redis stall (however
brief), an inline `await` would block the response on exactly the write this
whole design exists to keep off the critical path. Fast-but-synchronous is
still synchronous.

## The decision: fire, forget, but not carelessly

`emit_event()` schedules the `XADD` via `asyncio.create_task()` and returns
immediately — the request's own response is already being sent to the client
while the write happens on the event loop in the background. That's the
whole mechanism, and it's deliberately simple. Two sharp edges came with it,
both the kind of bug that passes every functional test and only shows up
once something in production actually depends on the thing quietly not
working:

**Task garbage collection.** Python's event loop holds a reference to a task
created via `asyncio.create_task()` only *weakly*. Without something else
holding a strong reference, the garbage collector is fully within its rights
to collect a task that hasn't finished yet — silently cancelling the write in
progress. This doesn't fail every time; it depends on scheduling and memory
pressure, which makes it exactly the kind of bug that looks fine in local
testing and drops events intermittently once conditions differ even slightly.
The fix is a module-level set holding a strong reference to every in-flight
task, with the reference removed via `add_done_callback` once the task
actually completes.

**Silent exceptions.** A fire-and-forget task's exception, by default,
propagates to nowhere anyone will ever see it — it doesn't crash the request,
doesn't log anything, doesn't increment any counter. It just vanishes. The
same `add_done_callback` used for the garbage-collection fix also inspects
`task.exception()` on completion, and turns a failure into both a log line
and a dedicated Prometheus counter (`shieldstream_event_emit_failures_total`)
instead of an event disappearing without a trace.

The stream itself is bounded — `MAXLEN ~ 100,000`, with the `~` meaning
*approximate*, not exact. Exact trimming would force Redis to check and
possibly trim on every single `XADD`; approximate trimming only happens at
efficient macro-node boundaries of the stream's internal structure, letting
the stream briefly exceed the nominal bound by a small margin in exchange for
meaningfully cheaper writes. Since consumers drain entries within seconds
under normal operation (Chapters 10 and 11), the approximation is
functionally exact in practice.

## Two smaller decisions worth naming

**The rate-limited (429) path emits its own event.** Blocked requests never
reach the proxy handler's own emit call — if nothing else emitted for them,
exactly the traffic a security gateway most needs visibility into (the
traffic it's actively rejecting) would be silently absent from the analytics
pipeline. The 429 path emits before it short-circuits, with
`latency_ms=0.0` by convention (there's no upstream round trip to measure for
a request that never reached the upstream), and the analytics consumer
(Chapter 10) explicitly excludes rate-limited events from latency
percentiles for exactly that reason — a stream of 0.0ms values mixed into a
p50/p99 calculation would corrupt it precisely during the traffic pattern
that matters most: an active attack generating a wall of blocked requests.

**Client IPs are hashed at the point of origin, not downstream.**
`SHA-256(ip + per-tenant salt)`, truncated to 16 hex characters, computed
before the IP ever leaves the request-handling code. An IP address is
personal data; hashing it immediately means no downstream component — the
stream, either consumer, the database — ever has the *choice* to mishandle a
raw address, because none of them ever receive one. The salt being
per-tenant (not global) is deliberate too: the same client IP hashes
differently across different tenants, which makes cross-tenant correlation
of one client's activity impossible by construction, while the hash stays
stable *within* one tenant — which is exactly what the anomaly detector in
Chapter 11 needs ("is this the same source hitting us as before," without
ever needing to know what that source's real IP actually is).

## What was verified

Twenty requests produced exactly twenty stream entries, every field present
and correctly string-typed (Redis Stream fields are strings; a boolean like
`rate_limited` is serialized as `"1"`/`"0"`, not Python's `str(True) ==
"True"`, so every consumer parses every field the same consistent way).
`remote_ip_hash` values were confirmed as 16-character hex digests, never a
raw address. 429 responses were confirmed producing events tagged
`rate_limited=1`.

The latency claim — that emission adds no measurable cost — was checked
carefully rather than assumed from the design alone. A first measurement
attempt was discarded outright: five hundred requests fired against a policy
limited to 100 per 60 seconds meant most of those responses were fast 429s,
which silently skewed the *overall* latency distribution downward in a way
that had nothing to do with emission's actual cost. Re-measured with a
status-code distribution that was actually checked before the number was
trusted: p50 came to 11.2ms at concurrency 5, with emission enabled, against
an 8.4ms baseline from Chapter 6 without it. A Jaeger span breakdown
attributed that entire delta to the rate limiter's own check (0.3–2.7ms) plus
ordinary variance — emission itself appears in no span and adds no
measurable request-path time, exactly as the design intended. Stream
trimming was verified experimentally too, not by reading the `MAXLEN`
argument in the code and trusting it: 150,000 pipelined writes against a
`MAXLEN ~ 100,000` bound left `XLEN` at exactly 100,000.
