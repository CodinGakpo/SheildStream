# Chapter 13 — Hot-Reloading Policy Without Losing the Safety Net

The policy cache from Chapter 8 uses a flat ten-second TTL. That's a
perfectly reasonable default for routine configuration changes, and it's
badly wrong for one specific, important scenario: an operator, mid-incident,
tightening a rate limit on a tenant that's actively attacking the system.
Ten seconds is an eternity when someone is actively trying to stop damage in
progress.

## The instinct to avoid: replacing the TTL instead of layering on top of it

The obvious-sounding fix is to push cache invalidation over Redis Pub/Sub —
the moment a policy changes, publish an invalidation message, and every
gateway replica evicts the stale entry instantly. That's the right mechanism.
It becomes the wrong *design* the moment it's treated as a replacement for
the TTL rather than an addition on top of it, because Pub/Sub messages are
not guaranteed delivery. A subscriber that's briefly disconnected, mid-restart,
or simply not yet caught up when a message fires will never see it — Redis
Pub/Sub has no replay, no queue, no "catch me up on what I missed." A
system that relies on Pub/Sub *alone* for correctness has a real, silent
failure mode: a missed message means a stale policy stays stale *forever*,
with nothing left to correct it.

The decision made here: Pub/Sub is a *latency optimization only*. The TTL
never goes away. A lost or delayed invalidation message degrades the system's
behavior to "stale for up to ten seconds" — the exact behavior Chapter 8
already built and verified — never to permanent staleness. This is the same
shape of reasoning that runs through the rate limiter's fail-open design
(Chapter 8) and the consumers' crash-recovery logic (Chapter 10): a fast path
for the common case, sitting on top of a slower but unconditionally correct
path that never gets removed just because the fast path usually works.

## A guide bug that would have made the whole feature silently inert

The guide's cache-invalidation listener reconstructs the Redis key it's
supposed to delete as `policy:{tenant_id}:{route_pattern}` — for example,
`policy:<id>:/proxy/*`, using the glob pattern stored in the policy record
itself. But the actual cache, built back in Chapter 8, keys its entries by
the *literal request path* that was actually requested —
`policy:<id>:/proxy/get`, never the glob it matched against. Those two keys
never collide. The guide's own invalidation code, run exactly as written,
would silently miss every real cache entry, every single time — Pub/Sub
messages would fire, the listener would run, `DEL` would execute against a
key that was never populated in the first place, and nothing would actually
be invalidated. The feature would appear to work in the sense that it runs
without erroring, while doing precisely nothing.

The fix doesn't try to reconstruct which literal paths happen to be cached —
it doesn't need to know. On receiving an invalidation for a tenant, the
listener `SCAN`s for every key matching `policy:{tenant_id}:*` and deletes
all of them, regardless of which specific literal paths they represent. It
trades a small amount of Redis-side scanning cost for not needing to solve a
problem (reverse-engineering which literal paths are currently cached from a
glob pattern) that doesn't actually need solving.

## A second guide bug, this one a real cross-tenant vulnerability

The guide's policy-update endpoint is written as `UPDATE policies SET ...
WHERE id = :id AND policy_version = :expected_version` — optimistic locking
on the version number, with no `tenant_id` anywhere in the `WHERE` clause at
all. Policy IDs aren't secret, and every tenant authenticates to the same
endpoint using only their own API key. Put those two facts together and the
guide's own endpoint, run as written, lets *any* authenticated tenant patch
*any other tenant's* policy, simply by guessing or enumerating IDs — a
textbook insecure direct object reference.

The fix adds `AND tenant_id = :tenant_id` directly into the update statement
itself, and — just as importantly — the fallback path that runs when the
update matches zero rows re-checks existence scoped by `tenant_id` too. That
second detail matters: without it, a cross-tenant request could still
distinguish "this policy doesn't exist" from "this policy exists but isn't
yours" by which error code comes back, leaking information about another
tenant's IDs even while correctly blocking the write. The fix returns a
plain `404` for both cases — verified live by attempting to patch one
tenant's policy ID using a *different* tenant's API key, and confirming the
response is `404`, not `403` (which would confirm the ID exists) and not
`200` (which would be the vulnerability itself).

## Three local environment problems this feature ran straight into

Building a persistent Pub/Sub listener, and separately testing it under
horizontal scaling, surfaced problems that had nothing to do with the
feature's own logic and everything to do with infrastructure it now
depended on for the first time.

**A shared Redis client tuned for the wrong job.** The existing hot-path
Redis client (Chapter 8) is deliberately tuned with an aggressive 0.2-second
socket timeout, exactly right for fail-open behavior on the request path.
Reused naively for `pubsub.listen()` — a call that's *supposed* to block for
a long time waiting on the next message — that same short timeout makes a
perfectly healthy, idle connection look identical to a dead one, producing a
constant reconnect storm. The fix: a second, separate Redis client with no
aggressive timeout, used only by the invalidation listener and the
dashboard's fan-out loop (Chapter 14).

**SELinux's exclusive bind-mount flag, recurring in a more serious form.**
Chapter 2 mentioned this in passing as a minor local annoyance — the `:Z`
bind-mount flag grants exclusive, relabeled access to exactly *one*
container. This week it stopped being minor. Scaling the gateway to two
replicas, specifically to verify multi-replica WebSocket fan-out (Chapter
14), failed the second replica outright with `Could not import module
"app.main"` — both containers were bind-mounting the same source directory
with the exclusive flag, and the second one to start simply couldn't read
it. The identical bug turned out to already be latent, unnoticed, between
the two consumer processes from Chapters 10 and 11, which have *always*
bind-mounted the same directory — it just hadn't manifested yet, because it
depends on a cold-start race that a `docker compose down && up` finally
triggered. Both were fixed by switching to `:z` (lowercase — shared
relabeling, not exclusive), which keeps SELinux enforcement active without
locking out a second concurrent reader.

**Docker's port-range allocator doesn't pick the lowest free port.** Testing
horizontal scaling needed a port range, not a single fixed mapping, so each
replica could bind a distinct host port. After the scaling test concluded and
the gateway was scaled back down to one replica, it did *not* rebind to the
original port — even though the OS-level socket for that port was confirmed
genuinely free on every attempt (a raw Python `bind()` call succeeded).
Repeated stop/remove/recreate cycles kept climbing to the next port in the
range instead. The range mapping was reverted to a single fixed port once the
scaling verification was complete, since every other tool and script in the
project assumes one specific, stable address — with a note to widen it
again, deliberately, if live scaling ever needs testing a second time.

## What was verified live

Dropping a tenant's rate limit from 100 to 2 requests via the new admin
endpoint enforced the new limit within roughly 0.1 seconds — six requests
fired, the third one tripped the new limit, the whole exchange completing in
0.126 seconds total, against the old TTL-only path's up-to-ten-second delay.
Optimistic locking was confirmed both ways: a stale expected version returns
`409`, a cross-tenant policy ID returns `404` (Section above).

The backstop itself — the actual failure mode this whole design exists to
survive, not just the happy path — was tested directly by updating a policy
through raw SQL, deliberately bypassing the admin endpoint and therefore the
publish step entirely. The gateway kept enforcing the *old* cached limit for
several seconds afterward, exactly as expected, and then — with no further
signal from anywhere — automatically picked up the new limit the instant the
ten-second TTL expired. That's the property this chapter is actually about:
not that the fast path works, but that the slow path silently and correctly
catches everything the fast path misses.

Multi-replica fan-out was verified the same way: with the gateway running as
two replicas on separate ports, a SQL-injection request routed through
replica A produced an identical alert delivered to a dashboard client
connected to replica B — confirming it's the Redis Pub/Sub bridge doing the
work, not any state living inside one gateway process, which is the only way
this design could possibly work across genuinely independent processes.
