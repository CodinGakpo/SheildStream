"""Alert deduplication within a short time window.

A naive detector emits one alert per matching event. During a real attack —
hundreds of SQLi probes within seconds from an automated scanner — that
produces hundreds of near-identical alerts, which is precisely the moment
alerts matter most and precisely when a flood trains whoever's watching to
tune them out (the well-documented "alert fatigue" failure mode). Modeled on
how Alertmanager / PagerDuty collapse duplicates: alerts sharing the same
(type, source) within a 60s window become one alert carrying a running
count, rather than N separate ones.
"""

import time

DEDUP_WINDOW_S = 60
# Expired-window entries are reset lazily on access, but entries for keys
# that never recur would otherwise linger forever — and `source_key` is
# attacker-controlled (an IP hash), so an attacker rotating source could grow
# `_recent` without bound: a memory-exhaustion vector. Sweep expired entries
# periodically (time-gated so a burst doesn't pay an O(n) sweep per event).
_SWEEP_EVERY_S = 5.0

_recent: dict[tuple[str, str], dict] = {}  # (alert_type, source_key) -> {first_seen, count}
_last_sweep = 0.0


def _sweep(now: float) -> None:
    global _last_sweep
    if now - _last_sweep < _SWEEP_EVERY_S:
        return
    _last_sweep = now
    expired = [k for k, e in _recent.items() if now - e["first_seen"] > DEDUP_WINDOW_S]
    for k in expired:
        del _recent[k]


def should_publish(alert_type: str, source_key: str) -> tuple[bool, int]:
    """Return (publish?, count). The first occurrence in a fresh window
    publishes immediately with count 1; subsequent ones within the window are
    suppressed but keep incrementing the count for the eventual summary."""
    now = time.time()
    _sweep(now)
    key = (alert_type, source_key)
    entry = _recent.get(key)

    if entry is None or now - entry["first_seen"] > DEDUP_WINDOW_S:
        _recent[key] = {"first_seen": now, "count": 1}
        return True, 1

    entry["count"] += 1
    return False, entry["count"]
