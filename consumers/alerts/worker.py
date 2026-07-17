"""Alert consumer: the second, independent reader of request_events.

`alert-cg` reads the exact same stream Week 6 produces and Week 7's
`analytics-cg` drains, from its own independent offset — the Streams-native
publish-once/consume-many pattern. Neither group's lag, backlog, or crash
history has any bearing on the other's; a single XADD from the gateway feeds
two structurally independent pipelines with no extra gateway-side work.

Delivery semantics differ from the analytics consumer, deliberately. The
analytics consumer's job is DURABLE COUNTING, so it uses strict write-then-
ACK and replays its own pending batch on restart — a duplicate is fine
(idempotent upsert), a loss is not. This consumer's job is TIMELY DETECTION:
an alert is a real-time signal, and re-emitting a minutes-old alert from a
replayed pending batch on restart would be misdated noise, not useful
signal. So on restart it DROPS (ACKs without re-detecting) its own pending
entries, and during steady state it ACKs every message once scanned —
matched or not — so the PEL never grows unbounded on the overwhelming
majority of benign traffic. Missing a handful of alerts across a crash is an
accepted trade for not double-alerting; the underlying attack, if sustained,
re-triggers on the next live event anyway.
"""

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from alerts.dedup import should_publish
from alerts.rps_window import RollingRpsCounter
from alerts.rules import scan_event
from alerts.statistical import Z_THRESHOLD, evict_idle, score_rps

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("shieldstream.alerts")

REDIS_URL = os.environ["REDIS_URL"]

STREAM = "request_events"
GROUP = "alert-cg"
# Stable name (see analytics worker): lets this consumer find its own pending
# entries on restart to drain them. A random name each restart would orphan
# them into the PEL until XAUTOCLAIM's idle threshold — needless here, since
# on restart we only want to clear them, not reprocess them.
CONSUMER_NAME = os.environ.get("CONSUMER_NAME") or socket.gethostname()

ALERT_CHANNEL = "dashboard:alerts"
METRICS_CHANNEL = "dashboard:metrics"  # Week 9: gateway WS dashboard's live RPS chart
READ_COUNT = 200
BLOCK_MS = 1000
BASELINE_EVICT_EVERY_S = 300.0


@dataclass
class AlertEvent:
    tenant_id: str
    endpoint: str
    query_string: str
    user_agent: str
    remote_ip_hash: str
    timestamp_ms: int


def parse_alert_event(fields: dict[str, str]) -> AlertEvent | None:
    """Poison-tolerant parse (same discipline as the analytics consumer): a
    malformed entry is logged and dropped rather than crashing the loop into a
    redeliver-crash cycle. Only the fields this consumer actually needs are
    extracted; query_string/user_agent default to empty since older events may
    predate their addition."""
    try:
        return AlertEvent(
            tenant_id=fields["tenant_id"],
            endpoint=fields["endpoint"],
            query_string=fields.get("query_string", ""),
            user_agent=fields.get("user_agent", ""),
            remote_ip_hash=fields["remote_ip_hash"],
            timestamp_ms=int(fields["timestamp_ms"]),
        )
    except (KeyError, ValueError) as exc:
        logger.error("poison_event_dropped", exc_info=exc)
        return None


async def ensure_consumer_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def drop_own_pending(redis: Redis) -> None:
    """On restart, ACK-and-drop this consumer's own un-acked entries without
    re-detecting them — see the module docstring on why stale alerts are noise.
    Keeps the alert-cg PEL from carrying a dead run's backlog forever."""
    total = 0
    while True:
        entries = await redis.xreadgroup(GROUP, CONSUMER_NAME, {STREAM: "0"}, count=READ_COUNT)
        messages = entries[0][1] if entries and entries[0][1] else []
        if not messages:
            break
        await redis.xack(STREAM, GROUP, *[mid for mid, _ in messages])
        total += len(messages)
    if total:
        logger.info("dropped_stale_pending count=%d", total)


async def _publish(redis: Redis, payload: dict, channel: str = ALERT_CHANNEL) -> None:
    await redis.publish(channel, json.dumps(payload))


async def process_message(redis: Redis, event: AlertEvent, rps_window: RollingRpsCounter) -> None:
    # Tier 1: synchronous OWASP signature matching, deduplicated per source.
    for rule in scan_event(event.query_string, event.user_agent):
        publish, count = should_publish(rule, event.remote_ip_hash)
        if publish:
            await _publish(redis, {
                "type": "THREAT_DETECTED",
                "rule": rule,
                "severity": "HIGH",  # a signature match is high-confidence relative to Tier 2
                "tenant_id": event.tenant_id,
                "endpoint": event.endpoint,
                "source": event.remote_ip_hash,
                "count": count,
                "timestamp_ms": event.timestamp_ms,
            })
    # Tier 2 feed: record the arrival; scoring happens once per second in tick().
    rps_window.record(event.endpoint)


async def score_baselines(redis: Redis, ticks: list[tuple[str, float]]) -> None:
    for endpoint, current_rps in ticks:
        z = score_rps(endpoint, current_rps)
        if z is not None and z > Z_THRESHOLD:
            publish, count = should_publish("BEHAVIORAL_ANOMALY", endpoint)
            if publish:
                await _publish(redis, {
                    "type": "BEHAVIORAL_ANOMALY",
                    "severity": "MEDIUM",  # "unusual", not "malicious" — a lower-confidence signal
                    "endpoint": endpoint,
                    "z_score": round(z, 2),
                    "current_rps": current_rps,
                    "count": count,
                })


async def publish_metric_snapshot(redis: Redis, ticks: list[tuple[str, float]]) -> None:
    """Total RPS across all endpoints, published once per completed second so
    the dashboard's live chart has something to render during quiet periods
    with no alerts — alerts alone would leave the chart static except during
    an actual attack.

    Reuses this consumer's already-single-replica guarantee (rps_window.py's
    per-endpoint baseline is only correct with one consumer seeing the whole
    stream — see Week 8's DECISIONS.md entry) rather than adding separate
    leader-election plumbing just to keep exactly one dashboard:metrics
    publisher across gateway replicas. If the loop stalled across multiple
    completed seconds, `ticks` bundles them together and this reports one
    combined snapshot rather than one per second — the same accepted
    multi-second-stall approximation already made for Tier 2 scoring above.
    """
    total_rps = sum(rps for _, rps in ticks)
    await _publish(
        redis,
        {"type": "METRIC_SNAPSHOT", "rps": total_rps, "ts": time.time()},
        channel=METRICS_CHANNEL,
    )


async def run() -> None:
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    await ensure_consumer_group(redis)
    await drop_own_pending(redis)

    logger.info("consumer_started name=%s group=%s", CONSUMER_NAME, GROUP)
    rps_window = RollingRpsCounter()
    last_evict = time.monotonic()

    while True:
        entries = await redis.xreadgroup(
            GROUP, CONSUMER_NAME, {STREAM: ">"}, count=READ_COUNT, block=BLOCK_MS
        )
        ack_ids: list[str] = []
        for _stream, messages in entries or []:
            for msg_id, fields in messages:
                # ACK every message once we're done with it, matched or not —
                # forgetting the non-matching majority would grow the PEL
                # without bound on normal traffic.
                ack_ids.append(msg_id)
                event = parse_alert_event(fields)
                if event is not None:
                    await process_message(redis, event, rps_window)

        if ack_ids:
            await redis.xack(STREAM, GROUP, *ack_ids)

        # Once per loop tick (~1s via block): finalize completed RPS seconds,
        # score them, and publish the dashboard's live RPS snapshot from the
        # same drained ticks — tick() pops its buckets, so it's called once
        # here and shared, not called again in each consumer separately.
        # Runs even when no new events arrived, so a traffic DROP to zero
        # still produces a per-second sample on both paths.
        ticks = rps_window.tick()
        await score_baselines(redis, ticks)
        await publish_metric_snapshot(redis, ticks)

        if time.monotonic() - last_evict >= BASELINE_EVICT_EVERY_S:
            evicted = evict_idle()
            last_evict = time.monotonic()
            if evicted:
                logger.info("evicted_idle_baselines count=%d", evicted)


if __name__ == "__main__":
    asyncio.run(run())
