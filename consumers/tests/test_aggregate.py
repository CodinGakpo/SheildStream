from datetime import datetime, timezone

from analytics.aggregate import (
    ParsedEvent,
    aggregate_by_bucket,
    minute_bucket,
    parse_event,
    percentile,
)


def make_event(**overrides) -> ParsedEvent:
    defaults = dict(
        tenant_id="t1",
        endpoint="/proxy/get",
        method="GET",
        status_code=200,
        latency_ms=10.0,
        rate_limited=False,
        timestamp_ms=1_700_000_030_000,  # :30s into some minute
    )
    defaults.update(overrides)
    return ParsedEvent(**defaults)


def raw_fields(**overrides) -> dict[str, str]:
    fields = {
        "request_id": "r1",
        "tenant_id": "t1",
        "endpoint": "/proxy/get",
        "method": "GET",
        "status_code": "200",
        "latency_ms": "10.5",
        "rate_limited": "0",
        "remote_ip_hash": "abc",
        "timestamp_ms": "1700000030000",
        "query_string": "",
        "user_agent": "curl",
    }
    fields.update(overrides)
    return fields


# --- parse_event -----------------------------------------------------------


def test_parse_event_roundtrip():
    e = parse_event(raw_fields())
    assert e is not None
    assert e.status_code == 200
    assert e.latency_ms == 10.5
    assert e.rate_limited is False


def test_parse_event_poison_returns_none_instead_of_raising():
    assert parse_event({"garbage": "data"}) is None
    assert parse_event(raw_fields(status_code="not-a-number")) is None


# --- bucketing --------------------------------------------------------------


def test_minute_bucket_truncates_to_the_minute_utc():
    b = minute_bucket(1_700_000_030_000)
    assert b.second == 0 and b.microsecond == 0
    assert b.tzinfo == timezone.utc
    assert b == datetime.fromtimestamp(1_700_000_030, tz=timezone.utc).replace(second=0)


def test_events_in_different_minutes_produce_different_rows():
    rows = aggregate_by_bucket(
        [make_event(), make_event(timestamp_ms=1_700_000_090_000)]  # next minute
    )
    assert len(rows) == 2


# --- counting ---------------------------------------------------------------


def test_counts_by_status_class_and_blocked():
    rows = aggregate_by_bucket(
        [
            make_event(status_code=200),
            make_event(status_code=201),
            make_event(status_code=404),
            make_event(status_code=502),
            make_event(status_code=429, rate_limited=True, latency_ms=0.0),
        ]
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.total_requests == 5
    assert r.status_2xx == 2
    assert r.status_4xx == 2  # the 429 counts as a 4xx AND as blocked
    assert r.status_5xx == 1
    assert r.blocked_count == 1


def test_rate_limited_events_excluded_from_latency_percentiles():
    rows = aggregate_by_bucket(
        [
            make_event(latency_ms=100.0),
            make_event(latency_ms=200.0),
            make_event(status_code=429, rate_limited=True, latency_ms=0.0),
        ]
    )
    (row,) = rows
    # 0.0 from the blocked event must not drag the percentile down.
    assert sorted(row.latencies) == [100.0, 200.0]
    values = row.as_tuple()
    p50, p99 = values[9], values[10]
    assert p50 >= 100.0
    assert p99 == 200.0


def test_all_blocked_bucket_has_null_percentiles():
    rows = aggregate_by_bucket(
        [make_event(status_code=429, rate_limited=True, latency_ms=0.0)]
    )
    values = rows[0].as_tuple()
    assert values[9] is None and values[10] is None


# --- percentile helper -------------------------------------------------------


def test_percentile_nearest_rank():
    values = sorted(float(v) for v in range(1, 101))  # 1..100
    assert percentile(values, 0.50) == 50.0
    assert percentile(values, 0.99) == 99.0
    assert percentile([42.0], 0.99) == 42.0
