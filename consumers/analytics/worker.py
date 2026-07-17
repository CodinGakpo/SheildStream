"""Analytics consumer: drains request_events into TimescaleDB.

At-least-once delivery, and the single most important ordering rule in this
process: XACK is called only AFTER the TimescaleDB write has committed —
never before, never in parallel. ACK-then-write would silently convert the
guarantee into at-most-once: a crash between the two loses the batch
forever (Redis considers it handled; the DB never saw it). Write-then-ACK
means the worst case on a crash is re-processing a batch that was already
written — a duplicate, not a loss — which is why the upsert must be (and
is) an idempotent increment.

Run as its own process (compose service `analytics-consumer`), never as a
background task inside the gateway: a stalled DB write in the gateway's
event loop would compete with live proxied requests for the same thread —
the exact coupling Week 6's fire-and-forget design exists to prevent.
"""

import asyncio
import logging
import os
import socket
import time

import asyncpg
from prometheus_client import Gauge, start_http_server
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from analytics.aggregate import aggregate_by_bucket, parse_event
from analytics.tracing import setup_tracing, span_for_event

# Consumer lag (pending, unacked entries in this group) — the Grafana
# dashboard's third panel (Week 10), and one of the blueprint's named alert
# thresholds (lag > 5000). A gauge, not a counter: lag needs to go down as
# well as up, and Prometheus scrapes it directly rather than the gateway
# proxying it — this consumer is the only process that can cheaply answer
# "how far behind is analytics-cg" via XPENDING's summary form.
CONSUMER_LAG = Gauge(
    "shieldstream_analytics_consumer_lag",
    "Pending (delivered but not yet ACKed) entries in analytics-cg",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("shieldstream.analytics")

REDIS_URL = os.environ["REDIS_URL"]
DATABASE_URL = os.environ["DATABASE_URL"]  # shieldstream_worker role — BYPASSRLS, see DECISIONS.md

STREAM = "request_events"
GROUP = "analytics-cg"
# STABLE name (container hostname), NOT a random per-process id. This is what
# makes crash recovery instant: on restart, the same-named consumer reclaims
# its own un-acknowledged entries directly via `XREADGROUP ... 0` (its own
# Pending Entries List), with no idle-time wait and no risk of stealing a
# peer's work — it's reclaiming what was already assigned to *it*. A random
# name each restart (as the guide uses) orphans the dead instance's PEL,
# recoverable only by the slow XAUTOCLAIM path below. See DECISIONS.md, Wk 7.
CONSUMER_NAME = os.environ.get("CONSUMER_NAME") or socket.gethostname()

BATCH_SIZE = 1000
BATCH_MAX_WAIT_S = 5.0
# Backstop for a consumer that dies *permanently* (scaled down, container
# replaced on deploy) and never comes back to reclaim its own PEL — a
# different consumer adopts its orphaned entries once they've been idle this
# long. Conservative on purpose: too low and a healthy-but-slow peer's
# in-flight work gets reclaimed out from under it. Self-recovery (above)
# needs none of this, which is why the kill-restart case is instant.
XAUTOCLAIM_IDLE_MS = 30_000
# How often the main loop sweeps for orphaned entries from a permanently
# dead peer (the self-recovery path handles our own restart at startup).
ORPHAN_SWEEP_EVERY_S = 15.0

# The composite PK (bucket, tenant_id, endpoint, method) was designed in
# Week 2 exactly for this statement. DO UPDATE *increments* — overwriting
# (SET x = EXCLUDED.x) would silently discard the row's prior accumulated
# counts every time a later batch touches the same minute bucket.
#
# Percentiles: merged as a count-weighted average, weights derived from
# existing columns (total - blocked = number of latency samples, since every
# non-blocked event contributes exactly one latency). A weighted average of
# two percentiles is NOT a true percentile of the combined distribution —
# exact merging needs a sketch structure (t-digest); documented, accepted
# approximation for a dashboard metric (DECISIONS.md, Week 7).
UPSERT_SQL = """
INSERT INTO request_metrics
    (bucket, tenant_id, endpoint, method, total_requests,
     status_2xx, status_4xx, status_5xx, blocked_count,
     p50_latency_ms, p99_latency_ms)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
ON CONFLICT (bucket, tenant_id, endpoint, method) DO UPDATE SET
    total_requests = request_metrics.total_requests + EXCLUDED.total_requests,
    status_2xx     = request_metrics.status_2xx     + EXCLUDED.status_2xx,
    status_4xx     = request_metrics.status_4xx     + EXCLUDED.status_4xx,
    status_5xx     = request_metrics.status_5xx     + EXCLUDED.status_5xx,
    blocked_count  = request_metrics.blocked_count  + EXCLUDED.blocked_count,
    p50_latency_ms = (
        COALESCE(request_metrics.p50_latency_ms, 0)
            * (request_metrics.total_requests - request_metrics.blocked_count)
        + COALESCE(EXCLUDED.p50_latency_ms, 0)
            * (EXCLUDED.total_requests - EXCLUDED.blocked_count)
    ) / NULLIF(
        (request_metrics.total_requests - request_metrics.blocked_count)
        + (EXCLUDED.total_requests - EXCLUDED.blocked_count), 0),
    p99_latency_ms = (
        COALESCE(request_metrics.p99_latency_ms, 0)
            * (request_metrics.total_requests - request_metrics.blocked_count)
        + COALESCE(EXCLUDED.p99_latency_ms, 0)
            * (EXCLUDED.total_requests - EXCLUDED.blocked_count)
    ) / NULLIF(
        (request_metrics.total_requests - request_metrics.blocked_count)
        + (EXCLUDED.total_requests - EXCLUDED.blocked_count), 0)
"""


async def ensure_consumer_group(redis: Redis) -> None:
    try:
        # id="0", not "$": the group's first creation starts at the very
        # beginning of the stream, so events emitted before this consumer's
        # first-ever startup aren't silently skipped. mkstream=True makes
        # startup order relative to the gateway's first XADD irrelevant.
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise  # group already existing is fine; anything else is real


async def flush_batch(pool: asyncpg.Pool, events: list, msg_ids: list, redis: Redis) -> None:
    """Write-then-ACK, in that order, always."""
    rows = aggregate_by_bucket(events)
    if rows:
        async with pool.acquire() as conn:
            # executemany runs inside an implicit transaction: the batch
            # commits atomically, so "written" below means all-or-nothing.
            await conn.executemany(UPSERT_SQL, [r.as_tuple() for r in rows])
    if msg_ids:
        await redis.xack(STREAM, GROUP, *msg_ids)
    logger.info("batch_flushed events=%d rows=%d acked=%d", len(events), len(rows), len(msg_ids))


async def recover_own_pending(redis: Redis, pool: asyncpg.Pool) -> None:
    """Instant self-recovery on restart: drain THIS consumer's own
    un-acknowledged entries (its PEL, from a prior run under the same stable
    name) via `XREADGROUP ... 0`. No idle-time wait — reclaiming your own
    assigned-but-unacked work is always safe, since by definition no other
    consumer holds it. Runs before consuming anything new."""
    total = 0
    while True:
        entries = await redis.xreadgroup(
            GROUP, CONSUMER_NAME, {STREAM: "0"}, count=BATCH_SIZE
        )
        messages = entries[0][1] if entries and entries[0][1] else []
        if not messages:
            break
        events = [e for _id, f in messages if (e := parse_event(f)) is not None]
        # Same write-then-ACK ordering as everywhere. Poison entries
        # (parse_event -> None) are ACKed without being written — dropped
        # deliberately, never left stuck in the PEL.
        await flush_batch(pool, events, [mid for mid, _ in messages], redis)
        total += len(messages)
    if total:
        logger.info("recovered_own_pending count=%d", total)


async def adopt_orphaned_pending(redis: Redis, pool: asyncpg.Pool) -> None:
    """Backstop: adopt entries left by a *different* consumer that died
    permanently, once they've been idle past the threshold. Swept
    periodically from the main loop, not just at startup."""
    cursor = "0-0"
    total = 0
    while True:
        cursor, claimed, _deleted = await redis.xautoclaim(
            STREAM, GROUP, CONSUMER_NAME, min_idle_time=XAUTOCLAIM_IDLE_MS,
            start_id=cursor, count=500,
        )
        if claimed:
            total += len(claimed)
            events = [e for _id, f in claimed if (e := parse_event(f)) is not None]
            await flush_batch(pool, events, [mid for mid, _ in claimed], redis)
        if cursor == "0-0" or not claimed:
            break
    if total:
        logger.info("adopted_orphaned_pending count=%d", total)


async def run() -> None:
    setup_tracing()
    start_http_server(9100)  # scraped by Prometheus (docker-compose.yml)
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await ensure_consumer_group(redis)
    await recover_own_pending(redis, pool)
    await adopt_orphaned_pending(redis, pool)

    logger.info("consumer_started name=%s group=%s", CONSUMER_NAME, GROUP)
    batch: list = []
    batch_ids: list[str] = []
    last_flush = time.monotonic()
    last_orphan_sweep = time.monotonic()

    while True:
        # Read at most what's left in the current batch, so a large backlog
        # drains as multiple bounded flushes instead of one giant
        # long-running transaction.
        entries = await redis.xreadgroup(
            GROUP, CONSUMER_NAME, {STREAM: ">"},
            count=max(1, BATCH_SIZE - len(batch)), block=1000,
        )
        for _stream, messages in entries or []:
            for msg_id, fields in messages:
                event = parse_event(fields)
                if event is not None:
                    batch.append(event)
                    # A short span, not wrapping the actual DB write (which
                    # happens later, batched, across many events at once) —
                    # its purpose is purely to give this event's original
                    # gateway trace a second span showing "the analytics
                    # consumer saw this," continuing the same trace rather
                    # than starting an unrelated one.
                    with span_for_event(event.traceparent):
                        pass
                # Poison entries still get ACKed at flush time — see
                # parse_event's docstring.
                batch_ids.append(msg_id)

        if batch_ids and (
            len(batch_ids) >= BATCH_SIZE
            or time.monotonic() - last_flush >= BATCH_MAX_WAIT_S
        ):
            await flush_batch(pool, batch, batch_ids, redis)
            batch, batch_ids = [], []
            last_flush = time.monotonic()

        if time.monotonic() - last_orphan_sweep >= ORPHAN_SWEEP_EVERY_S:
            await adopt_orphaned_pending(redis, pool)
            summary = await redis.xpending(STREAM, GROUP)
            CONSUMER_LAG.set(summary["pending"] if summary else 0)
            last_orphan_sweep = time.monotonic()


if __name__ == "__main__":
    asyncio.run(run())
