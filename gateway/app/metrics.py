"""Prometheus metrics — Week 10.

Label cardinality discipline: every label here is a small, bounded set of
values. `status_class` ("2xx"/"4xx"/"5xx"), never raw `status_code` — a raw
status code is still bounded in practice, but a `request_id` or tenant-level
label would not be (a new tenant means a new time series forever, and
Prometheus has no way to expire label combinations that stop appearing).
`endpoint` is deliberately NOT a label on any metric here for the same
reason: `/proxy/{path:path}` is user-controlled and unbounded.
"""

from prometheus_client import Counter, Histogram

REQUESTS_TOTAL = Counter(
    "shieldstream_requests_total",
    "Total requests handled by the gateway, by response status class",
    ["status_class"],
)

RATE_LIMIT_HITS_TOTAL = Counter(
    "shieldstream_rate_limit_hits_total",
    "Requests rejected with 429 by the sliding-window rate limiter",
)

# Default Prometheus buckets top out at 10s, tuned for slow web requests —
# far too coarse for a proxy whose own overhead budget is single-digit
# milliseconds (see DECISIONS.md Phase 2's ~6ms proxy-overhead finding).
PROXY_LATENCY_MS = Histogram(
    "shieldstream_proxy_latency_ms",
    "End-to-end proxy request latency, gateway-measured",
    buckets=(1, 2, 5, 8, 10, 15, 20, 30, 50, 100, 250, 500, 1000),
)

# Sub-2ms precision needed: this is the Lua script call itself, not the
# whole request — the guide's own target is a low single-digit-millisecond
# script (see Week 4's atomic sliding-window design).
REDIS_LUA_LATENCY_MS = Histogram(
    "shieldstream_redis_lua_latency_ms",
    "check_rate_limit's evalsha call latency",
    buckets=(0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 20, 50),
)


def status_class(status_code: int) -> str:
    return f"{status_code // 100}xx"
