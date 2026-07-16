import alerts.dedup as dedup
from alerts.dedup import should_publish


def setup_function():
    dedup._recent.clear()
    dedup._last_sweep = 0.0


def test_first_occurrence_publishes():
    assert should_publish("SQLI", "ip1") == (True, 1)


def test_burst_collapses_to_one_publish_with_growing_count():
    published = 0
    last_count = 0
    for _ in range(50):
        publish, count = should_publish("SQLI", "ip1")
        published += int(publish)
        last_count = count
    assert published == 1  # exactly one alert emitted for the burst
    assert last_count == 50  # but the count reflects all 50 attempts


def test_distinct_sources_dedup_independently():
    assert should_publish("SQLI", "ip1")[0] is True
    assert should_publish("SQLI", "ip2")[0] is True  # different source, own window


def test_distinct_types_dedup_independently():
    assert should_publish("SQLI", "ip1")[0] is True
    assert should_publish("XSS", "ip1")[0] is True  # same source, different alert type


def test_window_expiry_allows_republish(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(dedup.time, "time", lambda: t[0])
    assert should_publish("SQLI", "ip1") == (True, 1)
    assert should_publish("SQLI", "ip1")[0] is False
    t[0] += dedup.DEDUP_WINDOW_S + 1  # jump past the window
    assert should_publish("SQLI", "ip1") == (True, 1)  # fresh window, publishes again


def test_expired_entries_are_swept(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(dedup.time, "time", lambda: t[0])
    for i in range(10):
        should_publish("SQLI", f"ip{i}")  # 10 distinct source keys
    assert len(dedup._recent) == 10
    t[0] += dedup.DEDUP_WINDOW_S + dedup._SWEEP_EVERY_S + 1  # elapse past window + sweep gate
    should_publish("SQLI", "trigger-sweep")  # any call triggers the time-gated sweep
    # the 10 stale entries are gone; only the fresh trigger remains
    assert len(dedup._recent) == 1
