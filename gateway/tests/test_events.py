from app.events import RequestEvent, hash_ip


def make_event(**overrides) -> RequestEvent:
    defaults = dict(
        request_id="req-1",
        tenant_id="t1",
        endpoint="/proxy/get",
        method="GET",
        status_code=200,
        latency_ms=12.34,
        rate_limited=False,
        remote_ip_hash="abc123",
        timestamp_ms=1_700_000_000_000,
        query_string="q=1",
        user_agent="curl/8",
    )
    defaults.update(overrides)
    return RequestEvent(**defaults)


def test_to_redis_fields_all_values_are_strings():
    fields = make_event().to_redis_fields()
    assert all(isinstance(v, str) for v in fields.values())


def test_rate_limited_serializes_as_numeric_string():
    assert make_event(rate_limited=True).to_redis_fields()["rate_limited"] == "1"
    assert make_event(rate_limited=False).to_redis_fields()["rate_limited"] == "0"


def test_hash_ip_is_deterministic_and_not_the_raw_ip():
    h = hash_ip("203.0.113.7", "salt-a")
    assert h == hash_ip("203.0.113.7", "salt-a")
    assert "203.0.113.7" not in h
    assert len(h) == 16
    int(h, 16)  # valid hex


def test_hash_ip_differs_across_tenant_salts():
    # The property that makes cross-tenant correlation of a client impossible.
    assert hash_ip("203.0.113.7", "salt-a") != hash_ip("203.0.113.7", "salt-b")
