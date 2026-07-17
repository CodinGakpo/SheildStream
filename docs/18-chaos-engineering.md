# Chapter 18 — Killing Redis on Purpose

Chapter 8 built a fail-open design for the moment Redis becomes unreachable,
and proved it correct by actually killing the Redis container and watching
what happened. That was a real, live test — but it was also, necessarily, a
narrow one: a single kill, checked against a small number of requests, at a
specific point in the project's timeline. This chapter asks the same
question at a different scale, and in a more structured way: three fixed
phases — a healthy baseline, a real outage induced by stopping the actual
Redis container mid-traffic, and a recovery phase once it's restarted —
each measured with real concurrent load, not a handful of manual curl
requests.

## A bug in the chaos test itself, before any real finding could be trusted

The very first run of the chaos script produced an incomplete result: the
baseline and outage phases ran, but the recovery phase and the final summary
never appeared. The cause was in the script, not the system under test.
Locust's headless mode exits with a non-zero status code whenever a run
recorded *any* failures — a perfectly reasonable default, and exactly the
expected, informative outcome of an outage phase deliberately designed to
produce some failures. The chaos script itself was written with `set -e`,
which treats any non-zero exit as fatal and aborts the whole script
immediately. The moment the outage phase (correctly) recorded failures and
exited non-zero, the script stopped dead, silently skipping the recovery
phase and the comparison summary entirely — a script bug that would have
made every future run of this test *look* complete while quietly reporting
nothing, until someone actually checked whether all three phases had run.
Fixed by tolerating a non-zero exit specifically from each phase invocation:
a phase recording failures is data to collect, not a reason to stop
collecting more of it.

## The real finding: the fail-open code held up. Something else didn't.

With the script actually running all three phases, the result was, in the
best sense, uneventful where it mattered most: **zero evidence was found of
the fail-open logic itself ever failing.** Every request during the outage
window correctly logged that the tenant cache, the policy cache, and the
rate limiter's own Redis call were all unavailable — the exact log lines
Chapter 8 built specifically to make this failure mode visible — and each of
those paths degraded exactly as designed, falling through to Postgres or the
in-memory fallback. No uncaught exception was ever traced back to
`auth.py`, `policy.py`, or the rate limiter's fallback path, across the
entire outage window. The database connection pool, widened in Chapter 16 to
survive exactly this kind of thundering herd, held — even though *every*
request during the outage was now hitting Postgres twice (both authentication
and policy lookups cache-missing simultaneously, for the whole duration of
the outage), zero pool-exhaustion errors were logged.

The roughly 5% failure rate that *did* show up during the outage phase (67
failures out of 1,333 requests) traced somewhere else entirely: `httpbin`,
the downstream test service — the same single/few-worker development server
whose concurrency ceiling was first documented all the way back in Chapter
6. During the outage, every request takes roughly eighty times longer than
normal purely inside ShieldStream's own authentication and policy code
(around a second of database-fallback latency, against roughly thirteen
milliseconds normally) — entirely before the request ever reaches the point
of actually calling `httpbin`. That extra time means more requests are
genuinely in flight and reaching `httpbin` concurrently at any given moment
than during the healthy baseline, even though the load generator's own
request *rate* never changed. Some fraction of those pile up long enough in
`httpbin`'s own single-worker request queue to exceed the gateway's
two-second upstream connection timeout. This is the same known limitation
Chapter 6 flagged and explicitly deferred — a property of the test
harness's downstream stand-in, not a defect in ShieldStream's own logic —
now confirmed to matter under a second, independent kind of stress (a real
outage, not just raw concurrency) rather than only the one it was originally
found under.

## A dead end, tried and reported honestly rather than hidden

One hypothesis worth naming precisely because it *didn't* pan out: that
verbose debug-level logging (already measured as a real cost under heavy
load in Chapter 16) was adding enough overhead to the event loop to explain
the `httpbin` timeouts, and that switching to a quieter log level during the
outage phase would measurably reduce the failure rate. The experiment was run
directly — a repeat of the chaos test with logging turned down. It did not
show a clear improvement; if anything the outage-phase failure rate on that
run was *higher*, most plausibly ordinary run-to-run variance on shared,
single-core sandbox hardware rather than a real effect in either direction,
since one comparison run isn't a controlled experiment capable of
distinguishing a real signal from noise. The local development default was
reverted back to its original, more verbose setting — genuinely useful for
interactive debugging, and there was no actual evidence to justify changing a
working default. This is included in the record for the same reason every
other finding in this project is: a documented dead end, tried and reported
honestly, is worth more than a quietly discarded one that leaves the next
person to wonder whether it was ever considered at all.

## A finding outside what the script itself was measuring

Both consumer processes — the analytics consumer from Chapter 10 and the
alert consumer from Chapter 11 — crashed outright the moment Redis was
killed, with a plain `ConnectionError: Connection closed by server`. Neither
has an outer reconnect-with-backoff wrapper the way the gateway's own
Pub/Sub listener (Chapter 13) does. Restarting them manually turned into an
unplanned, and genuinely valuable, second verification: the analytics
consumer's own crash-recovery logic, built and proven in Chapter 10 through a
deliberately staged kill-and-restart test, now got to prove itself under a
*real*, unstaged crash instead — and it worked identically. The recovery
logs showed exactly the pattern Chapter 10 predicted: pending entries drained
completely on restart, zero loss, zero duplication.

This was left as a finding rather than turned into a code change, and the
reasoning is worth stating rather than leaving implicit: the production
deployment plan from Chapter 17 already configures every service to restart
automatically on crash, so a real deployment self-heals this without any
additional code. The local development environment deliberately sets no
restart policy anywhere, for any service — not a gap specific to these two
consumers, a consistent choice across the whole local stack, made so that a
crash surfaces immediately for a developer to actually look at, rather than
being silently and automatically masked by an endless restart loop that hides
the fact anything went wrong at all.

## The numbers, as measured

Baseline: 2,847 requests, zero failures. Outage: 1,333 requests, 67 failures
— 5.0%, traced to `httpbin`'s known concurrency ceiling rather than to
ShieldStream's own fail-open logic. Recovery: 2,886 requests, one failure —
0.03%, effectively back to baseline within ordinary measurement noise,
confirming the system recovers cleanly with no lingering degradation once
Redis returns — no connections stuck in a bad state, no cache left poisoned,
nothing that needed a manual nudge to fully heal.
