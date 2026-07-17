"""Week 11 load test: ramp 0->1000 users/60s, hold 5min, ramp down/60s.

Weighted mixed traffic — mostly normal GET/POST, plus a low-frequency SQLi
probe that exercises the Week 8 alert consumer's Tier-1 signature path
without dominating the request mix (a load test that's mostly attack
traffic wouldn't represent the p50/p99 targets, which describe steady-state
proxy overhead, not worst-case attack handling).

Run against the `loadtest` tenant (see seed_tenant.sql) — 5000 rps, not the
guide-default 100, so this measures proxy overhead, not the rate limiter's
429 path.
"""

import os

from locust import HttpUser, LoadTestShape, between, task

API_KEY = os.environ.get("LOADTEST_API_KEY", "sk_test_loadtest_fixedkey_localdev")

RAMP_UP_S = 60
HOLD_S = 300
RAMP_DOWN_S = 60
PEAK_USERS = 1000
SPAWN_RATE = 50  # users/sec — reaches PEAK_USERS well within RAMP_UP_S


class ShieldStreamUser(HttpUser):
    wait_time = between(0.5, 1.5)
    headers = {"X-API-Key": API_KEY}

    @task(70)
    def normal_get(self):
        self.client.get("/proxy/get", headers=self.headers, name="/proxy/get")

    @task(25)
    def normal_post(self):
        self.client.post(
            "/proxy/post", json={"probe": "shieldstream-loadtest"}, headers=self.headers, name="/proxy/post"
        )

    @task(5)
    def sqli_probe(self):
        # Low-frequency by design (5/100 tasks) — enough to keep the alert
        # consumer's signature path exercised under load, not enough to
        # dominate the latency distribution the p50/p99 targets describe.
        self.client.get(
            "/proxy/get",
            params={"q": "' OR 1=1--"},
            headers=self.headers,
            name="/proxy/get [sqli-probe]",
        )


class RampUpHoldRampDown(LoadTestShape):
    def tick(self):
        run_time = self.get_run_time()

        if run_time < RAMP_UP_S:
            user_count = max(1, round(PEAK_USERS * (run_time / RAMP_UP_S)))
            return (user_count, SPAWN_RATE)

        if run_time < RAMP_UP_S + HOLD_S:
            return (PEAK_USERS, SPAWN_RATE)

        total = RAMP_UP_S + HOLD_S + RAMP_DOWN_S
        if run_time < total:
            remaining = total - run_time
            user_count = max(1, round(PEAK_USERS * (remaining / RAMP_DOWN_S)))
            return (user_count, SPAWN_RATE)

        return None  # stop
