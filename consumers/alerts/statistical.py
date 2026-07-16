"""Tier 2 — EWMA baseline + z-score anomaly scoring, per endpoint.

"Normal" for an endpoint has to adapt over time without a single legitimate
burst instantly redefining normal to include itself. An Exponentially
Weighted Moving Average does exactly that with one parameter: each new
sample nudges the running mean only ALPHA of the way toward itself, so a
one-off spike barely moves the baseline while a sustained shift gradually
pulls it along. No training data, no model file, no offline fitting — it is
operational from the very first sample, at the cost of a cold-start period
before the baseline is trustworthy (see MIN_SAMPLES_BEFORE_SCORING).

The z-score (how many std-devs a sample sits from the mean) turns that
baseline into a threshold. z > 3 covers ~99.7% of variation UNDER A NORMAL
distribution — and request traffic is frequently NOT normal (it's often
bursty / Poisson-ish), so this is a cheap, interpretable heuristic, not a
rigorous statistical test. That honest caveat is the point of the MEDIUM
severity these alerts carry.
"""

import time
from dataclasses import dataclass, field

ALPHA = 0.1
Z_THRESHOLD = 3.0
MIN_SAMPLES_BEFORE_SCORING = 20  # cold-start guard — see module docstring / update_and_score
# Endpoints that stop receiving traffic keep a stale baseline forever
# otherwise; evict any not updated within this window so `_baselines` stays
# bounded even if the endpoint space is large (same bound-the-dict discipline
# as the Week 5 fallback limiter). Generous, because re-learning a baseline
# from cold isn't free.
BASELINE_TTL_S = 3600.0


@dataclass
class EndpointBaseline:
    mean: float = 0.0
    var: float = 0.0  # EWMA of squared deviation from the mean — a variance proxy
    samples_seen: int = 0
    last_seen: float = field(default_factory=time.monotonic)

    def update_and_score(self, value: float) -> float | None:
        self.samples_seen += 1
        self.last_seen = time.monotonic()
        if self.samples_seen == 1:
            self.mean = value
            return None

        deviation = value - self.mean
        self.mean += ALPHA * deviation
        self.var = (1 - ALPHA) * (self.var + ALPHA * deviation**2)

        if self.samples_seen < MIN_SAMPLES_BEFORE_SCORING:
            # A baseline from one or two samples is noise: its std-dev estimate
            # is tiny and unstable, so the very next ordinary value can register
            # an enormous spurious z-score. Suppress scoring until it settles.
            return None

        std_dev = max(self.var**0.5, 1e-6)  # floor avoids divide-by-zero on a near-flat baseline
        return deviation / std_dev


_baselines: dict[str, EndpointBaseline] = {}


def score_rps(endpoint: str, current_rps: float) -> float | None:
    """Update the endpoint's baseline with this second's RPS and return its
    z-score, or None while still in the cold-start window."""
    baseline = _baselines.get(endpoint)
    if baseline is None:
        baseline = _baselines[endpoint] = EndpointBaseline()
    return baseline.update_and_score(current_rps)


def evict_idle(now: float | None = None) -> int:
    """Drop baselines untouched for longer than BASELINE_TTL_S. Returns the
    number evicted. Cheap and called periodically from the worker loop."""
    cutoff = (now if now is not None else time.monotonic()) - BASELINE_TTL_S
    stale = [ep for ep, b in _baselines.items() if b.last_seen < cutoff]
    for ep in stale:
        del _baselines[ep]
    return len(stale)
