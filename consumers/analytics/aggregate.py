"""Pure aggregation logic for the analytics consumer.

Kept free of Redis/Postgres imports so the correctness-critical parts
(bucketing, counting, percentile math, poison-event tolerance) are unit
testable with no infrastructure at all — the worker loop around this is
thin plumbing verified by the live kill-and-restart test instead.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("shieldstream.analytics.aggregate")


@dataclass
class ParsedEvent:
    tenant_id: str
    endpoint: str
    method: str
    status_code: int
    latency_ms: float
    rate_limited: bool
    timestamp_ms: int
    traceparent: str = ""  # Week 10 cross-process tracing; absent on pre-Week-10 events


def parse_event(fields: dict[str, str]) -> ParsedEvent | None:
    """Poison-tolerant parse. A malformed entry (a bug in a future producer
    version, or junk written to the stream by something else) must never
    wedge the consumer in a crash-redeliver-crash loop — at-least-once
    redelivery would hand the consumer the exact same poison entry forever.
    Malformed events are logged and dropped (the caller still XACKs them);
    losing one broken event is strictly better than losing the pipeline."""
    try:
        return ParsedEvent(
            tenant_id=fields["tenant_id"],
            endpoint=fields["endpoint"],
            method=fields["method"],
            status_code=int(fields["status_code"]),
            latency_ms=float(fields["latency_ms"]),
            rate_limited=bool(int(fields["rate_limited"])),
            timestamp_ms=int(fields["timestamp_ms"]),
            traceparent=fields.get("traceparent", ""),
        )
    except (KeyError, ValueError) as exc:
        logger.error("poison_event_dropped", exc_info=exc)
        return None


def minute_bucket(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).replace(
        second=0, microsecond=0
    )


def percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile over an already-sorted list:
    rank = ceil(p * n), 1-based — e.g. p50 of [1..100] is 50, p99 is 99."""
    assert sorted_values
    rank = math.ceil(p * len(sorted_values))
    return sorted_values[max(0, rank - 1)]


@dataclass
class AggregatedRow:
    bucket: datetime
    tenant_id: str
    endpoint: str
    method: str
    total_requests: int = 0
    status_2xx: int = 0
    status_4xx: int = 0
    status_5xx: int = 0
    blocked_count: int = 0
    latencies: list[float] = field(default_factory=list)

    def as_tuple(self) -> tuple:
        # Percentiles computed only from non-rate-limited events (latencies
        # list excludes them — see aggregate_by_bucket): a blocked request
        # has no upstream round trip, and its conventional 0.0ms would drag
        # the percentiles into meaninglessness during an attack, which is
        # precisely when the dashboard is being watched.
        lat = sorted(self.latencies)
        p50 = percentile(lat, 0.50) if lat else None
        p99 = percentile(lat, 0.99) if lat else None
        return (
            self.bucket,
            self.tenant_id,
            self.endpoint,
            self.method,
            self.total_requests,
            self.status_2xx,
            self.status_4xx,
            self.status_5xx,
            self.blocked_count,
            p50,
            p99,
        )


def aggregate_by_bucket(events: list[ParsedEvent]) -> list[AggregatedRow]:
    """Collapse raw events into one row per (minute bucket, tenant,
    endpoint, method) — many events become few rows, cutting upsert volume
    before the database ever sees it."""
    rows: dict[tuple, AggregatedRow] = {}
    for e in events:
        bucket = minute_bucket(e.timestamp_ms)
        key = (bucket, e.tenant_id, e.endpoint, e.method)
        row = rows.get(key)
        if row is None:
            row = rows[key] = AggregatedRow(bucket, e.tenant_id, e.endpoint, e.method)

        row.total_requests += 1
        if e.rate_limited:
            row.blocked_count += 1
        else:
            row.latencies.append(e.latency_ms)

        if 200 <= e.status_code < 300:
            row.status_2xx += 1
        elif 400 <= e.status_code < 500:
            row.status_4xx += 1
        elif e.status_code >= 500:
            row.status_5xx += 1

    return list(rows.values())
