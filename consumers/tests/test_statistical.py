import alerts.statistical as stat
from alerts.statistical import (
    MIN_SAMPLES_BEFORE_SCORING,
    EndpointBaseline,
    evict_idle,
    score_rps,
)


def setup_function():
    # Module-global baselines are shared state; isolate each test.
    stat._baselines.clear()


def test_first_sample_returns_none_and_seeds_mean():
    b = EndpointBaseline()
    assert b.update_and_score(10.0) is None
    assert b.mean == 10.0


def test_cold_start_suppresses_scoring_until_min_samples():
    b = EndpointBaseline()
    # The first MIN_SAMPLES_BEFORE_SCORING-1 samples are still warming up.
    for _ in range(MIN_SAMPLES_BEFORE_SCORING - 1):
        assert b.update_and_score(10.0) is None
    # The MIN_SAMPLES_BEFORE_SCORING'th sample is the first to score; a value
    # right at the mean scores a small z, not a spurious cold-start spike.
    z = b.update_and_score(10.0)
    assert z is not None
    assert abs(z) < 1.0


def test_sustained_spike_scores_high_after_warmup():
    b = EndpointBaseline()
    for _ in range(MIN_SAMPLES_BEFORE_SCORING + 5):
        b.update_and_score(10.0)  # establish a flat ~10 rps baseline
    z = b.update_and_score(1000.0)  # a massive spike
    assert z is not None and z > stat.Z_THRESHOLD


def test_score_rps_is_per_endpoint():
    for _ in range(MIN_SAMPLES_BEFORE_SCORING + 1):
        score_rps("/a", 10.0)
    # A brand-new endpoint starts its own cold start, unaffected by /a.
    assert score_rps("/b", 9999.0) is None


def test_evict_idle_removes_stale_baselines():
    score_rps("/a", 1.0)
    assert "/a" in stat._baselines
    # Force staleness by moving last_seen far into the past.
    stat._baselines["/a"].last_seen -= stat.BASELINE_TTL_S + 1
    assert evict_idle() == 1
    assert "/a" not in stat._baselines
