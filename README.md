# ShieldStream

A distributed API security gateway: reverse proxy, atomic Redis-backed
sliding-window rate limiting, a two-tier real-time threat detector (OWASP
signatures + statistical anomaly scoring), and a live WebSocket dashboard —
built end-to-end from a set of implementation guides, with every bug found
in those guides' own pseudocode documented and fixed rather than silently
copied. Full decision history, including every deviation and why: [`DECISIONS.md`](DECISIONS.md).

## Architecture

```
                         ┌──────────────┐
                         │    Client    │
                         └──────┬───────┘
                                │ HTTPS
                         ┌──────▼───────┐
                         │    Caddy     │  (prod only — automatic HTTPS,
                         │ reverse proxy│   replaces the guide's AWS ALB)
                         └──────┬───────┘
                                │
                  ┌─────────────▼──────────────┐        ┌────────────────┐
                  │   ShieldStream Gateway      │◄──────►│  Next.js       │
                  │   (FastAPI, horizontally    │  wss   │  Dashboard     │
                  │   scalable — proven via     │        │  (Recharts,    │
                  │   live multi-replica test)  │        │  live RPS +    │
                  │  ┌────────────────────────┐ │        │  threat feed)  │
                  │  │ auth · rate limiter     │ │        └────────────────┘
                  │  │ (atomic Redis Lua)      │ │
                  │  │ policy cache (Pub/Sub   │ │
                  │  │ hot-reload + TTL        │ │
                  │  │ backstop) · admin API   │ │
                  │  │ · /metrics · /ws/       │ │
                  │  │ dashboard fan-out       │ │
                  │  └────────────────────────┘ │
                  └───────┬──────────────┬───────┘
                          │ XADD         │ proxied request
                 ┌────────▼────────┐     ▼
                 │      Redis      │  ┌──────────────┐
                 │ rate-limit ZSETs│  │  Tenant's own │
                 │ Streams (events)│  │   upstream    │
                 │ Pub/Sub (policy │  └──────────────┘
                 │ invalidate +    │
                 │ dashboard feed) │
                 └───┬─────────┬───┘
      consumer group │         │ consumer group
        analytics-cg │         │ alert-cg (independent offset,
                      ▼         ▼  same stream, zero producer coupling)
        ┌─────────────────┐  ┌──────────────────────┐
        │Analytics Consumer│  │   Alert Consumer      │
        │at-least-once,    │  │ OWASP signatures (Tier│
        │write-then-ACK    │  │ 1) + EWMA/z-score      │
        │→ TimescaleDB     │  │ anomaly (Tier 2) →     │
        │(request_metrics) │  │ dashboard:alerts +     │
        └────────┬─────────┘  │ dashboard:metrics      │
                  │            └──────────┬─────────────┘
        ┌─────────▼─────────┐             │ (Pub/Sub, back to
        │  Postgres/Neon,    │             │  the gateway's fan-out
        │  RLS-enforced,     │             │  above)
        │  two DB roles      │             │
        └────────────────────┘             ▼
                                    Prometheus + Grafana + Jaeger
                                    (metrics, alerting, cross-process
                                     tracing — gateway ⇄ analytics
                                     consumer, one continuous trace)
```

**What actually got built vs. the original design:** the blueprint's initial
draft specified AWS ECS/ElastiCache/RDS/ALB and a third "audit consumer" to
S3. The deployed shape is deliberately different — a hybrid free-tier
target (Neon, one VM, Vercel, Caddy — see [`infra/DEPLOY.md`](infra/DEPLOY.md))
in place of ~$65/month of AWS, and two consumers, not three (an audit trail
consumer was scoped out; `audit_events` exists in the schema for future
use but nothing writes to it yet). Every such deviation from the original
plan is recorded in `DECISIONS.md` at the point it was made, not
retrofitted here.

## What it does

- **Reverse proxy** with per-tenant API-key auth, upstream passthrough,
  credential stripping before forwarding.
- **Atomic distributed rate limiting** — a single Redis Lua script per
  request, sliding-window (not fixed-window, which allows a 2x boundary
  burst), fails open to a per-replica in-memory fallback if Redis itself is
  unreachable (live chaos-tested — see below).
- **Real-time threat detection**, two tiers: OWASP signature matching
  (SQLi/XSS/path-traversal, ReDoS-safe) and a statistical anomaly detector
  (EWMA baseline + z-score) that needs no training data and starts scoring
  from the first request.
- **Sub-second policy hot-reload**: an admin PATCH commits to Postgres, then
  pushes a Redis Pub/Sub invalidation — the old 10s cache TTL stays as a
  correctness backstop for a replica that misses the push, live-verified by
  actually dropping a message and confirming the TTL still recovers it.
- **Live dashboard** — WebSocket fan-out from the gateway (any replica, via
  Redis Pub/Sub — proven live across replicas, not just in-process),
  exponential-backoff auto-reconnect, real-time RPS chart + severity-tagged
  threat feed.
- **Full observability**: Prometheus metrics (cardinality-disciplined),
  structured JSON logs correlated by `request_id`, and cross-process
  distributed tracing — a single Jaeger trace spans the gateway *and* the
  downstream analytics consumer, a different OS process reading the event
  off Redis Streams well after the original request returned.
- **Chaos-tested, not just unit-tested**: `loadtest/chaos_redis_outage.sh`
  kills the real Redis container mid-traffic and confirms the fail-open
  design holds under actual load, not just in a mocked test.
- **Load-tested at 1000 concurrent users** (`loadtest/`) — three real
  bottlenecks (a connection pool, a DB pool, a container file-descriptor
  limit) found and fixed from the test's own failure output, not
  anticipated in advance. Results committed: [`loadtest-results/report.html`](loadtest-results/report.html).

## Running it locally

```bash
docker compose up -d
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate python seed.py   # prints tenant API keys once
```

- Gateway: `http://localhost:8000` (`/health`, `/metrics`, `/ws/dashboard`)
- Dashboard: `cd dashboard && npm install && npm run dev` → `http://localhost:3000`
- Grafana: `http://localhost:3001` (admin/localdev_only) · Prometheus: `http://localhost:9090` · Jaeger: `http://localhost:16686`

Run the test suites:
```bash
cd gateway && .venv/bin/pytest        # or: pip install -r requirements-dev.txt && pytest
cd consumers && pytest
cd e2e && npm install && npx playwright test   # needs the compose stack running
```

Load test (separate profile, not part of default `up`):
```bash
docker compose exec -T postgres psql -U shieldstream -d shieldstream < loadtest/seed_tenant.sql
docker compose --profile loadtest up loadtest
```

## Repo layout

| Path | What |
|---|---|
| `gateway/` | FastAPI proxy — auth, rate limiting, policy cache, admin API, dashboard WS, metrics/tracing/logging |
| `consumers/` | `analytics/` (Streams → TimescaleDB) and `alerts/` (two-tier threat detection → dashboard) |
| `dashboard/` | Next.js real-time operator dashboard |
| `db/` | Alembic migrations, seed script |
| `e2e/` | Playwright black-box tests against the real running stack |
| `loadtest/` | Locust load test + chaos test |
| `observability/` | Prometheus/Grafana provisioning (datasource, dashboard, alert rule) |
| `infra/` | Hybrid free-tier deployment artifacts — prepared, not provisioned (see `infra/DEPLOY.md`) |
| `DECISIONS.md` | Every deliberate deviation from the source guides, with the live verification that justified it |
