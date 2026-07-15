# ShieldStream — Final-Year Portfolio Project Blueprint
### Designed for: Adidev Anand | Target: FAANG Backend / Platform / Systems Engineering

---

## 1. PROJECT OVERVIEW

### Name: **ShieldStream**
### Tagline: *"A distributed API security gateway with real-time threat telemetry — built to handle the hard problems at the intersection of systems engineering and security."*

**Elevator Pitch:**

ShieldStream is a production-grade, horizontally-scalable API security proxy that sits transparently in front of any HTTP service. It enforces distributed rate limiting (sliding-window, atomic, Redis-backed), detects anomalous traffic patterns in real-time using a streaming consumer pipeline, and fans out live telemetry to a WebSocket-powered dashboard — all without adding more than ~3ms of median latency to the proxied request. The hard problems inside: race-condition-free distributed counters under concurrent load, at-least-once event delivery with consumer group semantics, WebSocket fan-out without thundering herds, and zero-downtime hot-reload of security policies. Every design decision is a deliberate trade-off, not a default.

**Core Value Proposition:**

ShieldStream targets small-to-mid engineering teams who need a self-hosted, lightweight alternative to AWS WAF or Cloudflare's API Shield — without vendor lock-in or per-request billing. It serves a DevSecOps persona who needs observability and enforcement in a single deployable unit.

**Why this and not a CRUD app:** ShieldStream *is* a FAANG system design interview question. "Design a rate limiter," "Design a real-time analytics pipeline," and "Design an API gateway" are canonical interview problems — and you will have shipped every single one of them.

---

## 2. CANDIDATE-DRIVEN MOTIVATION

**What Adidev already has (and this project leverages directly):**

| Existing Skill | How It's Activated |
|---|---|
| FastAPI / async Python | The gateway proxy is a high-performance ASGI FastAPI app |
| Celery + Redis | Directly translates to Redis Streams consumer groups — same mental model, more powerful primitives |
| PostgreSQL | Extended with TimescaleDB for time-series metrics — same DB, new query patterns |
| Docker + GitHub Actions | Docker Compose locally, ECS Fargate + OIDC in prod — your existing CI/CD knowledge scales |
| AWS SAA-C03 | Designing with ElastiCache, ECS, RDS, ALB, CloudWatch — cert knowledge becomes deployed reality |
| React / Next.js | Real-time WebSocket dashboard — your existing frontend reach closes the loop |
| Information Security degree | OWASP Top 10, threat modeling, attack pattern detection — you can speak with authority that no pure-CS grad can match |

**Gaps this project specifically closes:**

- **Real-time systems at scale:** You've used Redis as a queue via Celery. Here you'll operate Redis Streams directly — consumer groups, XACK semantics, consumer lag monitoring, redelivery on crash. This is the distributed systems muscle FAANG wants.
- **Distributed consistency:** You've written APIs that write to a single DB. Here you'll handle distributed rate-limit counters that must be atomic across 3 concurrent gateway replicas with no double-counting and no race conditions.
- **WebSocket fan-out at scale:** DrDeepti gave you real-time slot conflict prevention but not the fan-out problem (broadcasting to N concurrent dashboard clients from M event producers). Here you solve it properly.
- **Observability engineering:** You've deployed things. Here you'll instrument them — Prometheus metrics, distributed tracing with OpenTelemetry, structured logging with correlation IDs, and alert-worthy dashboards. This is the gap between "I deployed it" and "I operate it."
- **Production load testing:** You haven't validated claims like "handles 5k RPS" with data. Here you will, using Locust, and the results will be on your README.

---

## 3. TECHNICAL ARCHITECTURE & COMPONENT BREAKDOWN

### System Diagram (Text)

```
                        ┌─────────────────────────────────────────────┐
                        │           Client (HTTP/HTTPS)                │
                        └────────────────────┬────────────────────────┘
                                             │
                        ┌────────────────────▼────────────────────────┐
                        │         AWS ALB (Layer 7 Load Balancer)      │
                        └──────┬──────────────────────┬───────────────┘
                               │                      │
               ┌───────────────▼──────┐  ┌────────────▼──────────────┐
               │  ShieldStream GW #1  │  │  ShieldStream GW #2       │
               │  (FastAPI / ASGI)    │  │  (FastAPI / ASGI)         │
               │  ┌────────────────┐  │  │  ┌────────────────┐       │
               │  │ Rate Limiter   │  │  │  │ Rate Limiter   │       │
               │  │ (Redis Lua)    │  │  │  │ (Redis Lua)    │       │
               │  ├────────────────┤  │  │  ├────────────────┤       │
               │  │ Anomaly Scorer │  │  │  │ Anomaly Scorer │       │
               │  │ (Rule Engine)  │  │  │  │ (Rule Engine)  │       │
               │  ├────────────────┤  │  │  ├────────────────┤       │
               │  │ Event Emitter  │  │  │  │ Event Emitter  │       │
               │  │ (Redis Streams)│  │  │  │ (Redis Streams)│       │
               │  └────────────────┘  │  │  └────────────────┘       │
               └──────────┬───────────┘  └──────────┬────────────────┘
                          │                          │
                          └────────────┬─────────────┘
                                       │
                          ┌────────────▼─────────────┐
                          │   AWS ElastiCache (Redis)  │
                          │  ┌─────────────────────┐  │
                          │  │ Rate limit counters  │  │
                          │  │ (Sorted Sets + Lua)  │  │
                          │  ├─────────────────────┤  │
                          │  │ Redis Streams        │  │
                          │  │ (request_events)     │  │
                          │  ├─────────────────────┤  │
                          │  │ WebSocket pub/sub    │  │
                          │  │ (Redis Pub/Sub)      │  │
                          │  └─────────────────────┘  │
                          └────────────┬──────────────┘
                                       │
              ┌────────────────────────┼────────────────────────────┐
              │                        │                            │
┌─────────────▼────────┐  ┌───────────▼──────────┐  ┌─────────────▼──────┐
│  Analytics Consumer  │  │  Alert Consumer       │  │  Audit Consumer    │
│  (Python Worker)     │  │  (Python Worker)      │  │  (Python Worker)   │
│  Aggregates metrics  │  │  Threshold detection  │  │  Batch writes      │
│  → TimescaleDB       │  │  → WebSocket fanout   │  │  → S3 (Parquet)    │
└─────────────┬────────┘  └───────────┬───────────┘  └────────────────────┘
              │                       │
┌─────────────▼────────┐  ┌───────────▼──────────────────────────────────┐
│  AWS RDS PostgreSQL  │  │  Dashboard (Next.js + WebSocket)              │
│  + TimescaleDB ext.  │  │  Live: RPS, error rate, threat score,         │
│  Time-series metrics │  │  top-blocked IPs, policy violations           │
└──────────────────────┘  └─────────────────────────────────────────────┘
```

---

### Component Breakdown

#### **A. Gateway Proxy (FastAPI / ASGI)**

- **Technology chosen:** FastAPI with `httpx.AsyncClient` for reverse-proxying, Uvicorn workers, Python 3.12
- **Alternatives considered:**
  - *Node.js / Express:* Would have been faster to prototype, but Python is your primary language and FastAPI's async ASGI performance is comparable for I/O-bound proxy workloads (benchmarks: ~15k RPS on a single core). More importantly, your anomaly scoring logic is in Python — keeping one runtime eliminates the serialization boundary.
  - *Django:* WSGI-first, adding ASGI support feels bolted on. FastAPI's native async + dependency injection is cleaner for middleware chains.
  - *Go:* 3x the throughput ceiling but 5x the development time for someone not fluent in it. For a 12-week project, the bottleneck is not the language runtime — it's Redis and Postgres I/O.
- **Trade-offs:** Python GIL limits CPU parallelism, but the proxy is almost entirely I/O-bound (network, Redis calls). Multiple Uvicorn workers + async handles this. If CPU-bound scoring becomes a bottleneck at Week 10's load test, you'll move scoring out of the request path entirely (it's already designed to be async-optional).
- **Risky assumption:** Async httpx does not add catastrophic latency overhead vs. a compiled proxy. Validate in Week 4 with `hey` or `wrk` against a baseline (nginx upstream) and a ShieldStream-proxied upstream.

#### **B. Distributed Rate Limiter (Redis + Lua)**

- **Technology chosen:** Redis Sorted Sets + Lua scripting for atomic sliding window rate limiting
- **Algorithm:** Sliding window log (not fixed window, not token bucket)
  - Each request adds `(timestamp_ms, request_id)` to a Sorted Set keyed `rate:{tenant}:{route}`
  - Atomically removes all members outside the window (ZREMRANGEBYSCORE) and counts remaining (ZCARD)
  - The entire operation is a single Lua script — atomicity is guaranteed, no MULTI/EXEC needed
- **Alternatives considered:**
  - *Token bucket:* More burst-friendly, but harder to audit ("why was I rate limited?" becomes non-obvious), and refill timing across distributed nodes needs clock sync.
  - *Fixed window:* Allows 2x burst at window boundary — the well-known flaw. Rejected.
  - *Redis INCR + TTL:* Simple fixed window, same flaw. Rejected.
  - *In-memory per-instance counters:* Non-distributed. With 2 gateway replicas, each instance only sees half the traffic — limits are violated. Hard rejected.
- **Trade-offs:** Redis Sorted Sets use ~60 bytes per request log entry. At 10k RPS with a 60-second window, that's ~36MB RAM — acceptable. At 100k RPS it becomes problematic; the migration path is to approximate counting with Redis HyperLogLog or switch to Nginx's `ngx_http_limit_req_module` for raw rate limiting and move ShieldStream to enrichment/telemetry only.
- **Risky assumption:** Redis as a single point of failure for rate limiting. Mitigation: ElastiCache with auto-failover (replicas). If Redis is unreachable, the gateway fails open (allows requests, logs the failure) — documented decision with a configurable `RATE_LIMIT_FAIL_OPEN` env var.

#### **C. Event Pipeline (Redis Streams)**

- **Technology chosen:** Redis Streams with two consumer groups: `analytics-cg` and `alert-cg`
- **Why Redis Streams, not Kafka:**
  - Kafka requires a Kafka broker (or KRaft cluster) — that's an entire additional service to run, monitor, and operate. On AWS alone, MSK (Managed Kafka) starts at ~$200/month. Redis Streams run on your existing ElastiCache instance.
  - Redis Streams give you the critical distributed systems features: consumer groups, `XACK` acknowledgement semantics, automatic redelivery on consumer crash (pending entry list), consumer lag monitoring. This is 90% of Kafka's value for this project's scale.
  - The ceiling: Redis Streams are not designed for data retention beyond weeks (memory-bound), can't be replayed from arbitrary offsets as elegantly as Kafka, and have no schema registry. If ShieldStream ever needed true log compaction or 30-day replay, you'd migrate to Kafka. That's a known, documented trade-off — not an oversight.
- **Alternatives considered:**
  - *RabbitMQ:* Message queue semantics (not a log), no consumer group replay, adds another service. Rejected.
  - *Celery + Redis (your existing knowledge):* Celery abstracts away Redis Streams and doesn't expose consumer lag monitoring. For a portfolio project, using Redis Streams directly shows you understand the primitive, not just the abstraction.
  - *PostgreSQL LISTEN/NOTIFY:* Great for low-throughput notifications, but not designed for high-volume event streaming. Would block under load.
- **Trade-offs:** Redis Streams are in-memory — if ElastiCache runs out of memory, old stream entries are evicted (MAXLEN trim). Analytics consumer must write to TimescaleDB before the stream trims. Consumer lag alerting catches this early.

#### **D. Time-Series Storage (PostgreSQL + TimescaleDB)**

- **Technology chosen:** PostgreSQL 16 with TimescaleDB extension, deployed on AWS RDS
- **Schema highlights:**
  - `hypertable: request_metrics` — partitioned automatically by `bucket` (1-minute intervals) with TimescaleDB continuous aggregates rolling up to hourly/daily.
  - Columns: `bucket TIMESTAMPTZ, tenant_id, endpoint, status_code, request_count, p50_ms, p99_ms, threat_score_avg, blocked_count`
  - TimescaleDB's automatic chunk pruning handles data retention (retain 90 days).
- **Alternatives considered:**
  - *InfluxDB:* Purpose-built for time-series but introduces a second database engine you'd need to operate. TimescaleDB lets you stay in PostgreSQL — same `psql`, same connection pooling, same backup strategy.
  - *ClickHouse:* Columnar OLAP, excellent for analytics at petabyte scale, but operationally complex and overkill for this project's throughput.
  - *Plain PostgreSQL without TimescaleDB:* Manual partitioning by time is tedious and error-prone. TimescaleDB automates this with a single `create_hypertable()` call.
- **Trade-offs:** TimescaleDB extension must be installed at RDS layer — uses RDS PostgreSQL with the TimescaleDB extension (supported by AWS). Single-node write path; if ingest rate exceeds ~50k rows/sec, you'd need batching (already implemented — analytics consumer batches 1,000 rows per write).

#### **E. Real-Time Dashboard (Next.js + WebSocket)**

- **Technology chosen:** Next.js 14 (App Router), native browser WebSocket API, Recharts for time-series visualization
- **WebSocket implementation:** FastAPI's native `WebSocket` endpoint. A Redis Pub/Sub channel (`dashboard:live`) receives metric snapshots every second from the alert consumer. The FastAPI WebSocket handler subscribes and fans out to all connected clients.
- **Alternatives considered:**
  - *Server-Sent Events (SSE):* Unidirectional (server→client only), no backpressure signaling from client. WebSocket gives bidirectional capability for future "acknowledge alert" interactions.
  - *Socket.io:* Adds ~50KB to the bundle, introduces a non-standard protocol with fallback mechanisms (long-polling). Native WebSocket over WSS is sufficient and cleaner.
  - *Polling:* 1-second polling is indistinguishable from WebSocket for the user, but wastes a connection per tick and doesn't scale to many dashboard clients. Rejected.
- **Trade-offs:** If 1,000 users have the dashboard open simultaneously, the FastAPI WebSocket fan-out becomes a bottleneck. Mitigation: Redis Pub/Sub means any gateway replica can publish; the WebSocket handler is the fan-out bottleneck. Solution documented for scale: move to a dedicated WebSocket server (e.g., Centrifugo) or use Redis Pub/Sub with multiple WebSocket server replicas behind a sticky-session load balancer.

#### **F. CI/CD (GitHub Actions + OIDC + ECS)**

- **Technology chosen:** GitHub Actions with OIDC IAM federation (no stored AWS keys), ECR for image registry, ECS Fargate for compute
- **Why Fargate over EC2:** No server management. Auto-scales task count. Billing is per-second of task runtime. For a project with variable load, Fargate's economics are better than paying for idle EC2 capacity.
- **Why not EKS (Kubernetes):** EKS adds significant operational overhead (control plane, node groups, pod scheduling, Helm charts). For 2 gateway replicas and 3 consumer workers, ECS task definitions are sufficient and demonstrably simpler. *The moment ShieldStream needs 50+ services and complex inter-service routing, EKS becomes the right call* — you can articulate exactly when you'd make that switch.

---

## 4. DECISION-DENSE FEATURES

### Feature 1: Atomic Distributed Sliding Window Rate Limiter

**The naive approach and why it fails:**
A developer's first instinct is: `count = redis.INCR(key); redis.EXPIRE(key, 60)`. This is a fixed window counter. The flaw: a client can send 100 requests in the last second of window N and 100 more in the first second of window N+1 — 200 requests in 2 seconds against a "100 req/min" limit. Under scrutiny, this is a well-known vulnerability that collapses your rate limiting guarantee.

**The chosen approach:**
Sliding window log with a Redis Sorted Set, executed atomically via Lua:
```lua
local key = KEYS[1]
local now = tonumber(ARGV[1])        -- current timestamp (ms)
local window = tonumber(ARGV[2])     -- window size (ms)
local limit = tonumber(ARGV[3])      -- max requests
local req_id = ARGV[4]              -- unique request ID

-- Remove expired entries
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
-- Count remaining
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, req_id)
  redis.call('PEXPIRE', key, window)
  return 1  -- allowed
end
return 0  -- blocked
```
The entire script runs atomically on a single Redis instance. No WATCH/MULTI/EXEC race conditions.

**How Adidev's background helps / what he must learn:**
You know Redis from Celery — now you go one level deeper into Redis primitives (Sorted Sets, ZREMRANGEBYSCORE, PEXPIRE). The Lua scripting is a new skill (~2 days to learn well), but your Python intuition for loops/conditions translates directly.

**Recruiter tear-down question:** *"What's the memory complexity of your sliding window approach under high traffic?"*
- Answer outline: O(n) where n = number of requests in the current window per key. At 10k RPS with a 60s window = 600k entries. At 64 bytes each (sorted set member) ≈ 38MB per rate-limit key. If you have 10,000 tenants each at 10k RPS... you'd switch to an approximate counter (Redis HyperLogLog loses exactness but cuts memory 99%). Alternatively, token bucket with periodic refill via a background job trades exactness for memory. The right answer depends on whether the SLA requires exact enforcement.

---

### Feature 2: At-Least-Once Event Delivery with Consumer Group Semantics

**The naive approach and why it fails:**
Write a log entry to PostgreSQL on every proxied request, in the request path. At 5k RPS, that's 5,000 synchronous DB writes per second — PostgreSQL's write throughput ceiling on RDS (db.t3.medium) is ~3,000-5,000 simple INSERTs/sec. Under load, your proxy latency balloons to 50ms+ as it waits for DB acknowledgement. The DB becomes a back-pressure source on the critical path.

**The chosen approach:**
Fire-and-forget to Redis Streams on the request path (`XADD` is O(1), ~0.1ms). An asynchronous consumer group (a separate Python process) drains the stream, batches 1,000 entries, and writes to TimescaleDB in a single bulk INSERT. The consumer uses `XREADGROUP` with `NOACK=False`, and only calls `XACK` after a successful DB write. If the consumer crashes mid-batch, Redis' Pending Entry List (PEL) holds unacknowledged messages. On restart, the consumer calls `XAUTOCLAIM` to reclaim stale pending entries. This is the at-least-once guarantee.

**How Adidev's background helps / what he must learn:**
Celery with Redis broker already taught you the mental model: tasks go into a queue, workers consume them, Celery ACKs on success. Redis Streams is that system without the abstraction layer — you're working with XADD, XREADGROUP, XACK, XAUTOCLAIM directly. New knowledge: understanding XPENDING and the PEL structure (~3 days), and designing idempotent DB writes (use INSERT ... ON CONFLICT DO NOTHING with a composite unique constraint on (bucket, tenant_id, endpoint)).

**Recruiter tear-down question:** *"You say at-least-once — so you can have duplicate events. How do you prevent that from corrupting your analytics?"*
- Answer outline: Duplicates arise when a consumer crashes after writing to DB but before XACK. The TimescaleDB write is idempotent by design: the analytics consumer upserts into a pre-aggregated table keyed by (1-minute bucket, tenant_id, endpoint). A duplicate event hitting the same bucket just increments the same counter — bounded, not corrupting. For exact-once semantics you'd need a transactional outbox pattern (write event and DB state in the same Postgres transaction, then relay), but the complexity cost is not justified for approximate metric aggregation.

---

### Feature 3: Hot-Reloadable Policy Engine

**The naive approach and why it fails:**
Hardcode rate limits and security rules as environment variables or a config file baked into the Docker image. To change a rule, you redeploy. A redeploy takes 2-3 minutes minimum, during which either the old rules apply or there's a traffic gap. For a security gateway, a 3-minute delay to block an active attack is unacceptable.

**The chosen approach:**
Policies (rate limits per tenant/route, blocked IP ranges, OWASP rule sets) are stored in PostgreSQL. At startup, gateway replicas load policies into a local in-process cache (`dict` in Python). A background coroutine polls for policy version changes every 5 seconds by checking a `policy_version` integer in a Redis key. When a version bump is detected, the coroutine fetches the diff and atomically swaps the in-process cache using `asyncio.Lock`. No restart required.

A future enhancement (documented but not built): use Redis Pub/Sub for instant policy invalidation instead of 5-second polling — adds <1ms propagation latency.

**Recruiter tear-down question:** *"There's a 5-second window between a policy update and propagation. An attacker can slip 5 seconds of requests through after you've blocked them. How do you handle this?"*
- Answer outline: This is a deliberate trade-off. Strong consistency (blocking until all replicas confirm the new policy) would require distributed coordination (2PC or Raft) — massive complexity for a 5-second improvement. For most threat scenarios, 5-second lag is acceptable. If it's not (active DDoS mitigation), the response is: (a) put the blocked IP/CIDR in the rate limiter Redis with a 0-request limit — this propagates in ~0.1ms because every request hits Redis; or (b) use AWS WAF for IP blocking (sub-second rule propagation at the ALB layer) and reserve ShieldStream policies for application-layer rules.

---

### Feature 4: Real-Time Anomaly Detection (Rule Engine + Statistical Baseline)

**The naive approach and why it fails:**
Load a TensorFlow/Keras model inline in the gateway and score every request. Your CNN experience from ReportMitra makes this tempting. Problems: (a) model inference adds 5-20ms per request, destroying your latency SLA; (b) the model runs in the same process as the proxy — a slow inference blocks the event loop; (c) updating the model requires redeploying the gateway.

**The chosen approach:**
Two-tier detection:
1. **Synchronous Rule Engine (on request path, ~0.5ms):** OWASP-pattern regex matching (SQL injection, XSS, path traversal) evaluated against request headers, query params, and body. Rules are loaded from the hot-reloadable policy engine. If matched: block immediately, emit `THREAT_DETECTED` event.
2. **Async Statistical Anomaly Detector (off request path):** The alert consumer maintains rolling 5-minute baseline windows (exponentially weighted moving average of RPS, error rate, status code distribution) per endpoint. New data points are scored via z-score. If z > 3.0 (3 standard deviations from baseline), a `BEHAVIORAL_ANOMALY` alert is raised and pushed to the dashboard via Redis Pub/Sub. No request is blocked by this — it's alerting only (too many false positives for automated blocking without human review).

**How Adidev's background helps:**
Your EWMA and z-score math (standard stats/ML curriculum) and your spaCy/Keras experience give you the vocabulary to implement and discuss this confidently. Your InfoSec degree means you can discuss OWASP rules from a security engineering perspective, not just "I copied some regex from Stack Overflow."

**Recruiter tear-down question:** *"Your anomaly detector uses z-score on a rolling window. What happens during a legitimate traffic spike — say, a viral event — versus a DDoS attack? How do you distinguish them?"*
- Answer outline: You can't, reliably, from traffic volume alone. This is a fundamental limitation of statistical anomaly detection — it's context-free. Mitigations: (1) use multiple signals (not just RPS, but RPS + error rate + status code distribution + request fingerprint diversity); a DDoS often has low request diversity (same User-Agent, same endpoint), while a viral event has high diversity. (2) Exponential backoff on the EWMA — the baseline updates slowly, so a sustained spike shifts the baseline rather than triggering forever. (3) The detector's output is an alert, not an automated block — human confirmation is required. This is the responsible design for a false-positive-sensitive system.

---

### Feature 5: Multi-Tenant Isolation

**The naive approach and why it fails:**
Use a single Redis Sorted Set key namespace and a single TimescaleDB schema for all tenants. When Tenant A is under attack and generates 500k rate-limit operations/sec, it consumes Redis CPU and memory that degrades Tenant B's rate limiter performance. This is the "noisy neighbor" problem — unacceptable in a security-critical system.

**The chosen approach:**
- **Redis:** Key namespacing by `tenant_id` (`rate:{tenant_id}:{route}`) is already in the rate limiter. Redis Cluster (or ElastiCache cluster mode) can distribute tenant key spaces across shards using consistent hashing. Tenants with high traffic can be pinned to dedicated shards via hash tags: `rate:{tenant_id}:{route}` where `{tenant_id}` is the hash tag.
- **TimescaleDB:** Row-level security (RLS) policies on the `request_metrics` table ensure that a tenant can only query their own rows. API queries include `SET app.tenant_id = '<id>'` and RLS policies enforce `WHERE tenant_id = current_setting('app.tenant_id')`. No separate schema per tenant needed.
- **Rate limit fairness:** Each tenant has a configurable `max_rps` limit stored in the policy engine. A misbehaving tenant is rate-limited by their own policy — they cannot affect other tenants' limits.

**Recruiter tear-down question:** *"If one tenant has 100x the traffic of others, how does your Redis design handle that? Won't that shard become a hotspot?"*
- Answer outline: Yes — consistent hashing without intervention leads to hotspots for high-traffic tenants. The solution is explicit hash tag assignment: move hot tenants to a dedicated Redis shard. In ElastiCache cluster mode, shard assignment is controlled by the hash slot, which is determined by the hash tag in curly braces. You can also use Redis Cluster's manual slot migration to rebalance. A more advanced approach is client-side sharding with a custom consistent hash ring — documented as a future enhancement with clear rationale for when it becomes necessary (i.e., when a single ElastiCache shard exceeds 80% CPU).

---

## 5. DATA HANDLING & INTEGRITY

### Data Model

```sql
-- Tenants
CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  api_key_hash TEXT NOT NULL UNIQUE,  -- bcrypt hash, never store plaintext
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Policies (hot-reloadable)
CREATE TABLE policies (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE,
  route_pattern TEXT NOT NULL,       -- glob: /api/v1/*
  rate_limit_rps INT NOT NULL,
  rate_limit_window_s INT NOT NULL,
  owasp_rules_enabled BOOLEAN DEFAULT TRUE,
  custom_rules JSONB,                -- [{pattern, action, severity}]
  policy_version INT NOT NULL DEFAULT 1,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Hypertable (TimescaleDB) for metrics
CREATE TABLE request_metrics (
  bucket TIMESTAMPTZ NOT NULL,        -- 1-minute intervals
  tenant_id UUID NOT NULL,
  endpoint TEXT NOT NULL,
  method TEXT NOT NULL,
  status_code_2xx BIGINT DEFAULT 0,
  status_code_4xx BIGINT DEFAULT 0,
  status_code_5xx BIGINT DEFAULT 0,
  blocked_count BIGINT DEFAULT 0,
  total_requests BIGINT DEFAULT 0,
  p50_latency_ms FLOAT,
  p99_latency_ms FLOAT,
  threat_score_avg FLOAT,
  PRIMARY KEY (bucket, tenant_id, endpoint, method)
);
SELECT create_hypertable('request_metrics', 'bucket');

-- Continuous aggregate for hourly rollups
CREATE MATERIALIZED VIEW request_metrics_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', bucket) AS hour,
       tenant_id, endpoint,
       SUM(total_requests) as total_requests,
       SUM(blocked_count) as blocked_count,
       AVG(threat_score_avg) as threat_score_avg
FROM request_metrics
GROUP BY 1, 2, 3;

-- Audit log (append-only, archived to S3)
CREATE TABLE audit_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type TEXT NOT NULL,           -- REQUEST_BLOCKED, POLICY_UPDATED, etc.
  tenant_id UUID,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

**Tricky parts:**
- `custom_rules JSONB` in policies enables schema-flexible rule storage without migration overhead for every new rule type. Queried with PostgreSQL's `@>` containment operator.
- The `request_metrics` primary key is intentionally composite — idempotent upserts from the analytics consumer use `INSERT ... ON CONFLICT (bucket, tenant_id, endpoint, method) DO UPDATE SET total_requests = EXCLUDED.total_requests + request_metrics.total_requests`.
- `api_key_hash` uses bcrypt (cost factor 12). The plaintext API key is shown once at tenant creation and never stored. Verification: `bcrypt.checkpw(provided_key, stored_hash)` — takes ~100ms, fine for the admin API, not on the hot request path (gateway uses a short-lived Redis-cached lookup instead).

### Consistency, Durability & Concurrency

- **Rate limiter:** Lua script atomicity on Redis. No distributed lock needed.
- **Policy updates:** Optimistic locking with `policy_version` integer. Admin API uses `UPDATE policies SET ... WHERE id = ? AND policy_version = ?` — if 0 rows affected, someone updated concurrently; return 409 Conflict.
- **Analytics writes:** Idempotent upserts with ON CONFLICT. Analytics consumer is the only writer to `request_metrics` — no concurrent write contention.
- **Audit log:** Append-only, never updated. Archived to S3 in Parquet format weekly by the audit consumer using batch writes.

### Migration Strategy

- **Tool:** Alembic (you know SQLAlchemy/Django ORM — Alembic is the standalone migration tool). Migrations run in CI before deploy.
- **Zero-downtime migrations:** Additive-only changes (add column with DEFAULT, add index CONCURRENTLY). Never rename or drop columns in a single migration — use the expand/contract pattern: add new column → backfill → update code → deprecate old column in a future migration.
- **TimescaleDB schema changes:** TimescaleDB chunks must be migrated carefully — always test on a staging DB with production-scale data first. Document the `timescaledb_toolkit` functions used.

---

## 6. SCALABILITY, PERFORMANCE & FAILURE DESIGN

### Bottleneck Forecasting

| Load | First Bottleneck | Scaling Tactic |
|---|---|---|
| 1k RPS | None — baseline | Single gateway + single Redis |
| 5k RPS | Redis CPU (Lua scripts) | ElastiCache cluster mode (3 shards) |
| 10k RPS | Gateway async concurrency | Add 2nd ECS task; ALB distributes |
| 50k RPS | TimescaleDB write throughput | Batch size ↑ to 5k rows; add read replica for dashboard queries |
| 100k RPS | Redis memory (sorted sets) | Switch to token bucket + HyperLogLog; shard by tenant |

The first actual bottleneck in this project's realistic load range (1k–5k RPS, based on load tests at Week 10) is **Redis CPU from Lua script execution**. Each Lua script invocation is ~0.2ms on a single Redis instance — at 5k RPS that's 1,000ms of Redis CPU per second (100% utilization of one core). Mitigation: ElastiCache cluster mode distributes Lua execution across shards. The load test will prove this empirically.

### Resilience Patterns

- **Circuit breaker on upstream services:** If the downstream service returns 5xx for >50% of requests in a 10-second window, ShieldStream opens a circuit breaker: returns 503 immediately without forwarding, for 30 seconds. Implemented with a simple state machine in Redis (`upstream:{service}:circuit_state` = CLOSED/OPEN/HALF_OPEN).
- **Retry with exponential backoff:** On `XADD` to Redis Streams, if Redis is temporarily unavailable, retry 3 times with 100ms, 200ms, 400ms backoff before failing open (allowing the request, logging locally).
- **Graceful degradation:** If the anomaly detection consumer falls behind by >10,000 events (consumer lag alert), the statistical model operates on stale baselines. This is acceptable — the rule engine still functions synchronously. The lag alert notifies the operator to scale the consumer.
- **Bulkheading:** The three consumer workers (analytics, alert, audit) are separate processes. A crash in the audit consumer (e.g., S3 write failure) does not affect the analytics or alert consumers.

### Observability Plan

- **Metrics:** Prometheus client library in the FastAPI gateway exposes `/metrics`. Key metrics: `shieldstream_requests_total{tenant, endpoint, status}`, `shieldstream_rate_limit_hits_total{tenant}`, `shieldstream_proxy_latency_ms{quantile}`, `shieldstream_redis_lua_latency_ms`. Scraped by Prometheus on ECS, visualized in Grafana.
- **Logs:** Structured JSON logging (Python `structlog` library) with fields: `request_id` (UUID, generated per request for correlation), `tenant_id`, `endpoint`, `latency_ms`, `upstream_status`, `rate_limited`, `threat_score`. Shipped to CloudWatch Logs via awslogs driver.
- **Traces:** OpenTelemetry SDK in the gateway. Trace spans: `gateway.inbound`, `rate_limiter.check`, `anomaly.score`, `proxy.forward`, `event.emit`. Exported to AWS X-Ray (OTLP exporter). This lets you trace a single request across all components.
- **Key alerts:** (1) Gateway p99 latency > 50ms sustained 2 minutes. (2) Redis consumer lag > 5,000 events. (3) Circuit breaker OPEN for any upstream. (4) Rate limit Lua script error rate > 0.1%.

---

## 7. DEVELOPMENT METHODOLOGY & TESTING

### Branching Strategy

```
main (protected, auto-deploys to prod)
  └── develop (integration branch)
        ├── feat/rate-limiter-lua      (feature branches)
        ├── feat/redis-streams-consumer
        └── fix/websocket-fanout-leak
```

PRs require: (1) all CI checks green, (2) self-review checklist (no hardcoded secrets, migration tested locally, load test not regressed).

### Testing Pyramid

**Unit tests (pytest, ~60% of test suite):**
- Rate limiter Lua script behavior: mock Redis with `fakeredis` library — test exact count semantics, window boundary behavior, concurrent calls
- Anomaly detector: feed synthetic time-series data, assert z-score thresholds trigger correctly
- Policy engine: test cache invalidation logic with mocked Redis responses
- Consumer workers: test batch write logic with a real test PostgreSQL instance (Docker)

**Integration tests (~30%):**
- Full request flow: use `httpx.TestClient` to send requests through the gateway against a mock upstream (a simple FastAPI echo server in Docker Compose test profile)
- Rate limiting under concurrency: `asyncio.gather(100 tasks)` all hitting the same rate limit — assert exactly `limit` allowed, rest blocked
- Consumer-to-DB flow: emit 100 events to Redis Streams, assert TimescaleDB has the correct aggregated rows within 5 seconds

**E2E tests (~10%):**
- Full Docker Compose stack (gateway + redis + postgres + consumers + dashboard)
- Simulate a login brute-force scenario: 200 rapid requests from same IP, assert rate limit triggers, assert dashboard shows spike, assert audit log has entries

**Load tests (Locust, separate stage):**
- Target: 1,000 concurrent users, 5,000 RPS sustained for 5 minutes
- Success criteria: p50 < 15ms, p99 < 50ms, error rate < 0.1%
- Run against a staging ECS environment, not production
- Results committed to `/loadtest-results/` directory in the repo — this is the chart you show on your GitHub README

### Test Doubles & Chaos

- `fakeredis` for Redis unit tests — no live Redis needed
- `responses` library for mocking external HTTP calls
- Chaos: introduce artificial Redis latency via `tc netem` in Docker Compose (`delay 100ms`) and assert the gateway handles it gracefully (fails open, logs the error). This is a 2-hour exercise that produces a great story.

---

## 8. DEPLOYMENT & DEVOPS

### Infrastructure as Code

- **Tool:** AWS CloudFormation (YAML), committed to `/infra/` directory. Why not Terraform? You're already AWS-native (SAA-C03), CloudFormation has zero provider plugin management, and it's directly integrated with CDK if you want to evolve. Document this choice explicitly.
- **Resources defined in IaC:**
  - ECS Cluster + Task Definitions (gateway × 2 tasks, consumers × 3 tasks)
  - ElastiCache Redis cluster (2 nodes, automatic failover)
  - RDS PostgreSQL 16 (db.t3.small, Multi-AZ disabled to save cost — documented with upgrade path)
  - ALB with HTTPS listener, ACM certificate
  - CloudWatch Log Groups, Prometheus workspace
  - S3 bucket for audit log archives (lifecycle: Glacier after 30 days)
- **Cost estimate (monthly, AWS free tier exhausted):**
  - ECS Fargate (5 tasks × 0.25 vCPU × 0.5GB): ~$12
  - ElastiCache cache.t3.micro: ~$15
  - RDS db.t3.micro: ~$15
  - ALB: ~$18
  - Data transfer, CloudWatch: ~$5
  - **Total: ~$65/month** — affordable for a portfolio project, documented in README

### CI/CD Pipeline (GitHub Actions)

```yaml
Stages:
1. lint         → ruff (Python), eslint (TypeScript) — fast, < 30s
2. test         → pytest (unit + integration) with Docker Compose test profile
3. build        → Docker buildx, multi-platform (linux/amd64), push to ECR
4. security     → Trivy image scan (CRITICAL vulnerabilities → fail the build)
5. migrate      → Alembic upgrade head (runs against staging DB via SSM param)
6. deploy       → ECS rolling update (25% min healthy, 200% max) via aws ecs update-service
7. smoke-test   → curl health check + single proxied request through staging gateway
```

**OIDC IAM:** GitHub Actions assumes an IAM role via OIDC (no stored AWS_ACCESS_KEY_ID). You've used this before — it's on your portfolio. Document the trust policy in the IaC.

### Rollback Strategy

- ECS rolling update keeps old task version running until new tasks are healthy. Rollback: `aws ecs update-service --task-definition <previous-revision>` — documented as a runbook in the repo.
- Database rollbacks: Alembic `downgrade -1` in a hotfix pipeline. The expand/contract migration pattern ensures downgrade safety.
- Feature flags: a `FEATURE_FLAGS` environment variable (JSON) loaded by the gateway at startup. Toggle anomaly detection off in production without redeployment.

---

## 9. THE TEAR-DOWN SCRIPT

### 15 Interview Questions & Answer Outlines

**Q1: "Why didn't you just use Kong or AWS API Gateway as your proxy? Why build your own?"**
- The point isn't that ShieldStream is production-ready for everyone — it's that building it teaches you exactly how Kong works internally. AWS API Gateway abstracts away rate limiting, but you can't observe it, customize it, or interview about it. When asked "how does distributed rate limiting work," you now have a specific, implemented answer. ShieldStream is a learning artifact that doubles as a demonstrable system. At a real company, you'd evaluate Kong — but you'd evaluate it from a position of understanding.

**Q2: "What happens if your Redis instance goes down during peak traffic?"**
- The gateway has a `RATE_LIMIT_FAIL_OPEN` config (default: true). If the Lua script returns a connection error, the gateway allows the request and logs a `REDIS_UNAVAILABLE` counter in memory. An in-memory fallback rate limiter (token bucket, per gateway instance, not distributed) kicks in with a 10x more permissive limit. This is a deliberate trade-off: availability over strict enforcement. If fail-closed were required (security-critical), you'd document the latency impact of waiting for Redis recovery. ElastiCache auto-failover typically completes in 60-90 seconds — the in-memory fallback bridges that gap.

**Q3: "Your sliding window uses a Sorted Set. At 10k RPS, what's the memory footprint and how does it degrade?"**
- At 10k RPS with a 60-second window and 1 million unique client IPs: 600k entries per key, ~38MB per key. With 1M clients, that's 38TB — clearly not viable at that scale. The architectural response: at 10k RPS, you move to a probabilistic approximate counter (Redis HyperLogLog for cardinality, Redis INCR with TTL for approximate counts). Exact sliding window semantics are reserved for per-tenant/per-API-key enforcement (limited cardinality), not per-IP enforcement at massive scale. This is exactly the trade-off Cloudflare made — approximate enforcement at the edge, exact at the application layer.

**Q4: "How do you ensure your consumer workers don't fall behind the stream permanently?"**
- Consumer lag is monitored via a Prometheus gauge (`XLEN(stream) - consumer_offset`). Alert threshold: 5,000 events. Remediation: (1) scale the analytics consumer to N parallel instances within the same consumer group — each instance claims a partition of the stream via `XREADGROUP COUNT 100` (competing consumers). Redis Streams consumer groups automatically load-balance across consumers. (2) If batching is the bottleneck, increase batch size (1k → 5k rows) with a 5-second max-wait timeout. (3) If TimescaleDB write throughput is the ceiling, enable TimescaleDB compression on old chunks to free IOPS.

**Q5: "What happens if two gateway replicas update the anomaly score baseline simultaneously?"**
- The statistical baseline lives in the alert consumer, not the gateway. There's only one alert consumer instance (no concurrent updates). If you scaled to multiple alert consumers (within the same consumer group), each would own a partition of tenant streams — no shared state between consumers. The baseline per tenant lives in memory within the owning consumer process, periodically checkpointed to Redis. This is a documented design constraint: a single alert consumer is the current design; multi-consumer scaling requires sharding tenants across consumers with explicit assignment.

**Q6: "How would you migrate from Redis Streams to Kafka if ShieldStream needed to scale to 1M RPS?"**
- Phase 1: Run both in parallel. Gateway emits to Redis Streams AND Kafka (dual-write, ~2ms overhead per request). Consumers drain both; validate Kafka consumer output matches Redis consumer output for 1 week. Phase 2: Gate all consumers on Kafka. Phase 3: Retire Redis Streams. The dual-write adapter is a single abstraction (`EventEmitter` interface with two implementations) already designed into the codebase — it's not a theoretical plan, it's a documented interface.

**Q7: "Your dashboard WebSocket fan-out — what's the maximum number of connected clients before it breaks?"**
- Theoretical max: a single FastAPI WebSocket endpoint can sustain ~1,000 concurrent connections before the async event loop is saturated (each connection is a coroutine). Measured max in load testing: run `k6` WebSocket load test. At >500 clients, Redis Pub/Sub message processing starts competing with WebSocket write coroutines. Mitigation path documented: add a dedicated WebSocket server (Centrifugo or Soketi) that subscribes to Redis Pub/Sub and handles fan-out natively — it's designed for 100k+ concurrent WebSocket connections. The current implementation is appropriate for an ops dashboard with <50 concurrent users.

**Q8: "How do you handle zero-downtime deployments with WebSocket connections open?"**
- ECS rolling update starts new tasks before stopping old ones. Existing WebSocket clients connected to old tasks are disconnected when the old task stops (after drain period). Clients automatically reconnect — the Next.js dashboard has exponential backoff reconnection logic. This is explicit: WebSocket connections are not stateful beyond a session — any gateway or WebSocket server can accept the reconnect. Documented trade-off: 1-3 second WebSocket disruption during deploy is acceptable for an ops dashboard. For production chat systems, you'd use connection migration (not supported by native WebSocket protocol) or sticky sessions.

**Q9: "What's your plan for GDPR compliance if ShieldStream logs IP addresses in request events?"**
- IP addresses in the EU are PII under GDPR. In the current architecture: (1) IP addresses are hashed (SHA-256 + per-tenant salt) before being written to Redis Streams and TimescaleDB — the hash is usable for counting unique IPs without storing the raw address. (2) Audit logs that need raw IPs (for law enforcement compliance) are encrypted at rest in S3 using customer-managed KMS keys, with a documented retention period and deletion workflow. (3) A `data_retention_days` policy field per tenant enables automated TimescaleDB chunk deletion. Information Security degree directly informs this design.

**Q10: "Why ECS over EKS? At what scale would you switch?"**
- ECS (with Fargate) is sufficient for <20 distinct services with straightforward load balancing. The ShieldStream deployment has 5 task types (gateway, 3 consumers, Prometheus). ECS task definitions and service auto-scaling handle this comfortably. The switch to EKS becomes justified when: (a) >20 microservices needing complex inter-service routing (service mesh), (b) multi-cloud portability requirements, (c) team has dedicated platform engineers for Kubernetes operations. The operational overhead of EKS (control plane management, Helm chart sprawl, RBAC complexity) is not justified below that threshold.

**Q11: "Your Lua rate limiter script — what if Redis is running in cluster mode and the key is on a different shard than where the script runs?"**
- Redis Cluster routes keys to shards via CRC16 hash of the key. Lua scripts can only operate on keys in the same slot. Solution: ensure all keys for a single rate-limit operation share the same slot by using hash tags: `rate:{tenant_id}:{route}` — the curly braces force Redis to hash only `tenant_id`, ensuring all keys for a tenant land on the same shard. This is why the key structure includes the hash tag — it's not accidental.

**Q12: "How do you test the anomaly detection component? You can't unit test 'anomaly' without real data."**
- Three levels: (1) Unit test the z-score calculation with synthetic time-series data (inject known anomalies at known positions, assert detection). (2) Integration test with a replayed traffic scenario — a recorded 30-minute traffic trace with an injected brute-force sequence at minute 15; assert the detector fires within 60 seconds of the injection. (3) Shadow mode validation: run the new model version alongside the old in production, comparing outputs without acting on the new model's decisions. Ship when the new model's false positive rate is lower on production traffic.

**Q13: "What's your database schema migration strategy without downtime?"**
- Expand/contract pattern: (1) Expand: add new column with a DEFAULT value (non-blocking DDL in Postgres 12+). Deploy code that writes to both old and new columns. (2) Backfill: background job updates NULL rows in batches with `pg_sleep(0.01)` between batches to avoid lock contention. (3) Contract: once all rows are populated and old column reads are removed from code, drop the old column in a future migration. For index creation: always `CREATE INDEX CONCURRENTLY` — it builds the index without holding an exclusive lock.

**Q14: "Your `THREAT_DETECTED` events — how do you prevent false positives from flooding the dashboard?"**
- Rate-limit the alerts themselves. In the alert consumer: if more than 100 `THREAT_DETECTED` events arrive from the same IP in 60 seconds, they're collapsed into a single alert with a `count` field. This is the same deduplication pattern used by PagerDuty and Alertmanager. Additionally, each alert has a `severity` (LOW/MEDIUM/HIGH) based on the OWASP rule matched — the dashboard only shows HIGH by default, with a toggle for MEDIUM. LOW events are logged but not surfaced. This prevents dashboard fatigue during a noisy scan.

**Q15: "If I give you a 1-hour load test showing your p99 is 120ms instead of your claimed 50ms, what's your debugging methodology?"**
- Structured debugging: (1) Check distributed trace in X-Ray — which span is slow? If `rate_limiter.check` is 80ms, Redis is the bottleneck. If `proxy.forward` is 80ms, the upstream service is slow (or DNS resolution). (2) Check CloudWatch metrics for Redis CPU and connection count. (3) Check TimescaleDB write latency — if the analytics consumer is backlogged, it's not the p99 issue (it's off the request path). (4) Check Uvicorn worker queue depth — if workers are queued, you need more ECS tasks. Each hypothesis has a specific metric to check before changing code. You don't guess — you measure.

---

## 10. LEARNING ROADMAP (12 WEEKS)

### Phase 1: Foundation (Weeks 1–3)
**Goal: Architecture is locked, local environment is running, core gateway works.**

| Week | Work | Deliverable / Verify |
|---|---|---|
| 1 | System design doc (1 page), draw architecture diagram, finalize tech decisions, set up monorepo structure | Architecture doc reviewed with a peer or mentor; no unanswered "why" questions |
| 2 | Docker Compose with all services (gateway placeholder, Redis, Postgres+TimescaleDB), Alembic migrations, schema created | `docker compose up` → all services healthy, `alembic upgrade head` succeeds |
| 3 | FastAPI gateway: basic reverse proxy (no rate limiting yet), health check endpoint, OpenTelemetry tracing instrumented | `curl http://localhost:8000/proxy/anything` → response forwarded; trace visible in Jaeger (local) |

**Resources:**
- Redis documentation: `redis.io/commands/xadd` through `xautoclaim` — read the full Streams tutorial
- *Designing Data-Intensive Applications* (Kleppmann) — Chapter 11 (Stream Processing): the theoretical foundation for everything in Phase 2
- FastAPI docs: dependency injection, middleware, WebSocket

---

### Phase 2: Core Systems (Weeks 4–7)
**Goal: Rate limiter is atomic, streams pipeline is running, anomaly detection is working.**

| Week | Work | Deliverable / Verify |
|---|---|---|
| 4 | Sliding window rate limiter: write Lua script, integrate into gateway middleware, unit tests with `fakeredis` | 100 concurrent requests against a limit-of-10 → exactly 10 allowed; load test shows p99 < 5ms for rate limit check |
| 5 | Redis Streams producer in gateway, analytics consumer with batched TimescaleDB writes, XACK semantics | Kill consumer mid-run → restart → no duplicate rows in DB; consumer lag stays < 1,000 |
| 6 | Alert consumer: z-score baseline, OWASP rule engine (regex for SQLi, XSS, path traversal) | Inject synthetic SQLi request → `THREAT_DETECTED` event in Redis within 100ms |
| 7 | Hot-reload policy engine: DB → Redis version check → in-process cache invalidation | Update rate limit via admin API → observe gateway using new limit within 5 seconds, no restart |

**Resources:**
- *The Art of PostgreSQL* (Fontaine) — TimescaleDB chapter equivalents online: `docs.timescale.com`
- OWASP Testing Guide (free, `owasp.org`) — understand the attacks your rule engine detects
- Redis Lua scripting guide: `redis.io/docs/manual/programmability/lua-api/`

---

### Phase 3: Frontend & Observability (Weeks 8–9)
**Goal: Dashboard is live, observability stack is running, everything is wired end-to-end.**

| Week | Work | Deliverable / Verify |
|---|---|---|
| 8 | Next.js dashboard: WebSocket connection, live RPS chart (Recharts), threat alert feed, rate limit status per tenant | Open dashboard → numbers update every second; simulate attack → red alert appears within 2 seconds |
| 9 | Prometheus metrics exposed by gateway, Grafana dashboards (RPS, p99, Redis CPU, consumer lag), CloudWatch log shipping | Grafana dashboard shows live data during local load test; alert fires when simulated Redis latency is injected |

**Resources:**
- Prometheus docs: client_python library
- OpenTelemetry Python getting started guide
- *Production Kubernetes* (Beyer et al.) — the observability chapters apply even without Kubernetes

---

### Phase 4: Performance & Deployment (Weeks 10–11)
**Goal: Load test data proves the system works, AWS deployment is live and reproducible.**

| Week | Work | Deliverable / Verify |
|---|---|---|
| 10 | Locust load test: 1,000 concurrent users, 5,000 RPS, 5 minutes. Profile, find bottleneck, fix it, re-test | p50 < 15ms, p99 < 50ms, error rate < 0.1% — results in `/loadtest-results/` with charts |
| 11 | CloudFormation IaC, ECS Fargate deployment, ElastiCache + RDS provisioned, GitHub Actions pipeline live, staging environment up | `git push main` → GitHub Actions builds, scans, deploys to ECS automatically; staging gateway accepts proxied requests |

**Resources:**
- AWS ECS Fargate workshop (free, `ecsworkshop.com`)
- Locust documentation: shape classes for custom load profiles
- Trivy: `trivy image` command for vulnerability scanning

---

### Phase 5: Polish & Portfolio (Week 12)
**Goal: The project looks as impressive as it is.**

| Task | Deliverable |
|---|---|
| Chaos experiment: kill Redis during load test, observe fail-open behavior | Documented in README with screenshot |
| Record 5-minute Loom walkthrough of the system | Linked prominently in GitHub README |
| Load test results chart | SVG chart in README |
| Architecture diagram (draw.io or Excalidraw) | In README |
| Write 3 blog posts (dev.to or personal site): rate limiter deep-dive, Redis Streams vs Kafka, TimescaleDB for beginners | Linked in README and LinkedIn |
| `DECISIONS.md` file in the repo | Every major tech decision documented with alternatives and trade-offs |

---

### Key Resources

| Topic | Resource |
|---|---|
| Distributed systems foundations | *Designing Data-Intensive Applications* — Kleppmann (Chapters 8, 11) |
| Redis mastery | Official Redis documentation + *Redis in Action* — Carlson |
| TimescaleDB | `docs.timescale.com` — continuous aggregates tutorial |
| System design interview prep | *System Design Interview Vol. 1 & 2* — Alex Xu |
| Observability | *Observability Engineering* — Majors, Fong-Jones, Miranda |
| Security foundations | OWASP Testing Guide v4.2 (free) |
| Load testing | Martin Fowler's blog on performance testing strategies |
| Real-world distributed systems | The morning paper (`adriancolyer.wordpress.com`) — pick any 3 papers on stream processing |

---

## APPENDIX: THE DIFFERENTIATOR ARGUMENT

When a FAANG interviewer asks "tell me about your most technically complex project," this is what you say:

> "I built a distributed API security gateway from scratch — not as an exercise, but to understand exactly how systems like AWS WAF and Kong work internally. The core hard problems were: making a rate limiter atomic across multiple server replicas without race conditions (I implemented a sliding window using Redis Lua scripts, which run atomically on the server), building an at-least-once event delivery pipeline using Redis Streams consumer groups without duplicating analytics data (idempotent upserts in TimescaleDB), and fanning out real-time telemetry to a live dashboard without creating a thundering herd problem. I load-tested it to 5,000 requests per second with p99 under 50ms. Every design decision in the system has a documented alternative I considered and rejected — and I can walk you through any of them."

That is not a project description. That is an interview answer that invites exactly the questions you are prepared to answer.

---

*Built for Adidev Anand — VIT Vellore, Information Security, 2026.*
*Target: Backend / Platform / Systems Engineering at FAANG.*
