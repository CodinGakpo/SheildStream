#!/bin/bash
# Week 12 chaos test: three fixed phases (baseline / outage / recovery)
# against a real Redis kill, not a simulated one. Verifies live what Weeks
# 3/5/9's fail-open code (app/auth.py, app/policy.py, app/fallback_limiter.py)
# only asserted in unit tests — that killing Redis mid-traffic degrades
# request handling (higher latency, DB falls back to being the sole source
# of truth) rather than failing it.
#
# Run from the repo root: ./loadtest/chaos_redis_outage.sh
set -euo pipefail
cd "$(dirname "$0")/.."

RESULTS_DIR="loadtest-results/chaos"
mkdir -p "$RESULTS_DIR"

USERS=50
SPAWN_RATE=10
PHASE_DURATION=60s

run_phase() {
    local phase="$1"
    echo "=== Phase: $phase ($PHASE_DURATION, $USERS users) ==="
    # `|| true`: Locust's headless mode exits nonzero when the run recorded
    # any failures — expected and informative during the outage phase, not
    # a script error. Without this, `set -e` above aborted the whole script
    # right after the outage phase on the first run, silently skipping
    # recovery and the summary entirely.
    docker compose --profile loadtest run --rm --no-deps loadtest \
        locust -f chaos_locustfile.py --headless \
        --host http://gateway:8000 \
        -u "$USERS" -r "$SPAWN_RATE" -t "$PHASE_DURATION" \
        --csv "/results/chaos/${phase}" --html "/results/chaos/${phase}.html" \
        || true
}

echo "--- Baseline: Redis healthy ---"
run_phase baseline

echo "--- Inducing outage: stopping Redis ---"
docker compose stop redis
run_phase outage

echo "--- Recovery: restarting Redis ---"
docker compose start redis
# Wait for Redis's own healthcheck, not a fixed sleep — the gateway's next
# request should already succeed as soon as Redis actually accepts
# connections again, so tying this to the healthcheck (not a guessed delay)
# keeps the recovery phase honest about when "recovered" really begins.
until [ "$(docker compose ps redis --format '{{.Health}}')" = "healthy" ]; do
    sleep 1
done
run_phase recovery

echo
echo "=== Error rate by phase ==="
for phase in baseline outage recovery; do
    # stats.csv's last data row is the "Aggregated" summary line.
    line=$(tail -1 "$RESULTS_DIR/${phase}_stats.csv")
    reqs=$(echo "$line" | cut -d',' -f3)
    fails=$(echo "$line" | cut -d',' -f4)
    echo "$phase: requests=$reqs failures=$fails"
done
