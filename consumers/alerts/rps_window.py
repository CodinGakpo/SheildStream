"""Per-endpoint requests-per-second counter, bucketed by wall-clock second.

Tier 2's baseline needs one RPS sample per endpoint per second. This counts
arrivals into integer-second buckets and, on each `tick()`, finalizes every
bucket for a second that has fully elapsed — yielding a true per-second count
rather than a smeared rolling estimate. An endpoint with no traffic in a
given second simply produces no sample that second (its baseline just isn't
updated), which is the correct behavior.

Single-consumer assumption: this counter lives in one process and is only
meaningful when a single alert-cg consumer sees the entire stream. Scaling
alert-cg to multiple consumers would shard events across them and make every
per-endpoint RPS an undercount — so the alert consumer runs as one replica by
design (documented in DECISIONS.md, Week 8), unlike the analytics consumer,
whose idempotent counting is safe to parallelize.
"""

import time


class RollingRpsCounter:
    def __init__(self, now_fn=time.time) -> None:
        self._now = now_fn
        self._buckets: dict[int, dict[str, int]] = {}  # second -> {endpoint: count}

    def record(self, endpoint: str) -> None:
        sec = int(self._now())
        bucket = self._buckets.setdefault(sec, {})
        bucket[endpoint] = bucket.get(endpoint, 0) + 1

    def tick(self) -> list[tuple[str, float]]:
        """Finalize and drain every fully-elapsed second (any bucket strictly
        before the current second). Returns (endpoint, rps) pairs. If the loop
        stalled across several seconds, each completed second is drained as its
        own set of samples, oldest first."""
        current = int(self._now())
        out: list[tuple[str, float]] = []
        for sec in sorted(s for s in self._buckets if s < current):
            for endpoint, count in self._buckets.pop(sec).items():
                out.append((endpoint, float(count)))
        return out
