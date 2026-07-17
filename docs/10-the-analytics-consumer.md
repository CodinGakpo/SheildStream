# Chapter 10 — At-Least-Once, Proven by Actually Crashing the Consumer

The analytics consumer's job sounds simple: drain the event stream from
Chapter 9 into TimescaleDB, so the numbers behind the dashboard (Chapter 14)
and the metrics behind Grafana (Chapter 15) actually mean something. The
headline requirement, stated at the outset and treated as non-negotiable
throughout, was durability: kill this process in the middle of writing a
batch, restart it, and the result has to be zero data loss *and* zero
double-counting. Not "should be," not "the logic implies it" — proven, by
actually killing it and checking.

It runs as its own OS process, not a background task inside the gateway,
and that's a direct consequence of the separation-of-concerns decision made
back in Chapter 2: a stalled database write here must never be able to
compete with a live proxied request for the gateway's own event loop, and the
only way to guarantee that is to not share an event loop with it at all.

## The one ordering rule that everything else depends on

`XACK` — acknowledging a Streams entry as processed — happens only *after*
the TimescaleDB write for that batch has committed, never before, and never
in parallel with it. This ordering is the entire durability guarantee, and
it's worth being explicit about why the reverse order fails: acknowledging
*before* writing would silently turn the at-least-once guarantee into
at-most-once — a crash landing between the acknowledgment and the write
would leave Redis believing the batch was handled while the database never
actually received it. Write-then-acknowledge means the worst case on a crash
is *re-processing* an already-written batch — a duplicate, not a loss — which
is exactly why the write on the other end has to be idempotent.

**The upsert that makes duplicates harmless:** `INSERT ... ON CONFLICT
(bucket, tenant_id, endpoint, method) DO UPDATE`, where the update
*increments* existing counts (`total_requests = request_metrics.total_requests
+ EXCLUDED.total_requests`) rather than overwriting them. Overwriting would
silently discard whatever a table row had already accumulated every time a
later batch happened to touch the same bucket. This was verified directly,
not just reasoned about: two separate 300-count batches applied against the
same key produced 600, not 300 — proof the increment behavior is real, not
assumed from reading the SQL.

## A recovery strategy that looks right on the page and loses data the moment you actually kill it

The implementation guide's crash-recovery design uses a random consumer name
generated fresh on every process start, and recovers *solely* via
`XAUTOCLAIM` with a 30-second idle threshold, run once at startup. This reads
as reasonable — `XAUTOCLAIM` is the standard Redis Streams mechanism for
reclaiming entries a dead consumer never acknowledged. The guide's own
kill-and-restart test description (kill, wait, restart, check) would have
exposed the problem immediately, and running it live did exactly that.

The failure mode: after a *fast* restart — the realistic case, the one that
actually matters operationally — the orphaned entries have been idle for far
less than the 30-second threshold. `XAUTOCLAIM` claims nothing, because
nothing has been idle long enough yet. Meanwhile the main consume loop reads
with `XREADGROUP ... >`, which only ever returns *never-delivered* messages —
never the ones that were already delivered to the old (differently-named)
consumer and left unacknowledged in its pending-entries list. With a random
name on every restart, that old consumer's identity is gone, and its pending
entries have no owner asking for them back yet. Five hundred test entries sat
permanently stuck: the database stayed at zero, `XPENDING` stayed at five
hundred, indefinitely — not eventually recovered, *never* recovered, because
nothing in the guide's design was ever going to ask for them again on this
timeline.

## The fix: a stable name, and two recovery mechanisms for two different failures

**A stable consumer name** — read from an environment variable, or falling
back to the container's own hostname — replaces the random name on every
restart. On restart, the *same-named* consumer can drain its own pending
entries instantly via `XREADGROUP ... 0`, a read that specifically means "give
me everything already assigned to me that I haven't acknowledged yet," with
no idle-time wait at all. This carries zero risk of stealing another
consumer's work, because by definition you can only be reclaiming entries
that were already assigned to *your own* identity.

That handles the fast-restart case perfectly, but it doesn't handle a
different failure mode: a consumer that dies *permanently* — scaled down,
replaced during a deploy, never coming back under that name again. Its
pending entries have a real owner, one that will never return to claim them.
For that case, the periodic `XAUTOCLAIM` sweep is kept as a backstop, run
every fifteen seconds from the main loop rather than only once at startup —
so a surviving consumer adopts genuinely orphaned work from a peer that's
truly gone, on an ongoing basis, not just at its own boot time.

Two mechanisms, each solving a distinct failure: instant self-recovery for
"I just restarted," and idle-timeout adoption for "someone else died and
isn't coming back."

## Other decisions, more briefly

Events are drained in micro-batches — 1,000 events or 5 seconds, whichever
comes first — bounded specifically so that a large backlog drains as several
smaller flushes rather than one giant, lock-holding transaction. The
aggregation logic (bucketing, counting, percentile math, tolerance for
malformed events) lives in a pure, dependency-light module, unit-tested with
zero infrastructure at all; the thin worker loop wrapped around it is covered
instead by the live kill-and-restart test, since that's the part where
infrastructure behavior — not pure logic — is actually what's being verified.
A malformed ("poison") event is logged, dropped, and *still acknowledged* —
never left to wedge the consumer into a crash-redeliver-crash loop on the
same unparseable entry forever. Percentile merging across batches uses a
count-weighted average rather than a true percentile of the combined
distribution — computing a genuine merged percentile needs a sketch structure
(a t-digest, for instance); the weighted-average approximation is a
documented, accepted trade-off for a dashboard metric, in line with the
guide's own stated position that approximate aggregation is acceptable here.

## Proving the durability claim, repeatably

The baseline: five hundred backlog events produce exactly five hundred
`total_requests` in the database, and `XPENDING` returns to zero.

The real test: kill the process *correctly* — specifically, within the
five-second batch window, so the five hundred entries are genuinely
delivered-but-unflushed at the moment of the kill, confirmed by checking
database count is zero and pending count is five hundred *at kill time*,
before restarting. Restart, and watch `recovered_own_pending count=500` in
the logs, followed by exactly five hundred rows in the database, zero
pending, zero duplicates. This was repeated three times, all three passing.

Worth stating plainly: the *first* attempt at this test was a false pass. The
kill landed a fraction too late — after the flush had already completed —
so what actually got proven was that normal operation works, not that
recovery does. That's a subtle, easy mistake (a passing test that's testing
the wrong thing looks identical to a passing test that's testing the right
thing), caught only by checking that the database count was genuinely still
zero at the moment of the kill, and redone with tighter timing until it
actually was. A green checkmark on the wrong test is worse than a visible
failure, because it hides exactly the gap it was meant to catch.

One more live check, this time about steady-state behavior rather than crash
recovery: two thousand requests fired under live load drained `XPENDING`
from 999 down to zero within about two seconds, with the database reaching
exactly two thousand and no double-counting of entries that had already been
acknowledged in an earlier pass.
