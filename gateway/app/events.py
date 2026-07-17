import hashlib
from dataclasses import asdict, dataclass

from fastapi import Request


@dataclass
class RequestEvent:
    request_id: str
    tenant_id: str
    endpoint: str
    method: str
    status_code: int
    latency_ms: float
    rate_limited: bool
    remote_ip_hash: str  # SHA-256(ip + per-tenant salt), never the raw IP
    timestamp_ms: int
    query_string: str = ""  # consumed by Week 8's OWASP rule engine
    user_agent: str = ""
    traceparent: str = ""  # W3C trace context — Week 10 cross-process tracing

    def to_redis_fields(self) -> dict[str, str]:
        """XADD field values must be strings. Bool is serialized as "1"/"0"
        (not str(True) == "True") so consumers parse it with int(), the same
        way as every other numeric field — one convention, not two."""
        d = asdict(self)
        d["rate_limited"] = int(d["rate_limited"])
        return {k: str(v) for k, v in d.items()}


def client_ip(request: Request) -> str:
    """REVISION #6: in any deployed topology (Caddy in Phase 6), the TCP peer
    is the reverse proxy, not the client. Rather than hand-parsing
    X-Forwarded-For here (trusting it blindly would let any direct caller
    spoof their IP), deployment runs uvicorn with --proxy-headers and
    --forwarded-allow-ips scoped to the proxy, which rewrites request.client
    from XFF only when the connection actually comes from that trusted hop.
    This function then stays a one-liner in both topologies. Lives here (not
    routes/proxy.py) because both the proxy handler and the rate limiter's
    429 path need it, and proxy -> rate_limit -> proxy would be a cycle."""
    return request.client.host if request.client else "unknown"


def hash_ip(ip: str, tenant_salt: str) -> str:
    """One-way, per-tenant-salted hash of the client IP.

    An IP address is PII under GDPR; hashing at the point of origin means no
    downstream component (stream, consumers, TimescaleDB) ever has the choice
    to mishandle the raw address, because it was never given it. The salt is
    per-tenant so the same client IP hashes differently across tenants —
    cross-tenant correlation of a client is impossible by construction, while
    within a tenant the hash is stable, which is all Week 8's anomaly
    detector needs ("is this the same source as before").
    """
    return hashlib.sha256(f"{ip}{tenant_salt}".encode()).hexdigest()[:16]
