"""Week 12 chaos test traffic generator.

Reuses locustfile.py's ShieldStreamUser (same weighted GET/POST/SQLi mix,
same loadtest tenant) but deliberately has no LoadTestShape — chaos_redis_
outage.sh drives it with plain `-u/-r/-t` flags for three short, fixed-
concurrency phases instead of one long ramp/hold/ramp-down.
"""

from locustfile import ShieldStreamUser  # noqa: F401 — Locust discovers it via import
