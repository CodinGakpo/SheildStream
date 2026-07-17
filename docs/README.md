# ShieldStream: The Decision Log, As a Book

`DECISIONS.md`, one level up, is the ground truth — a chronological log, one entry
per decision, written as it happened. It's precise, and it's terse by design: a
reference document, meant to be grepped, not read start to finish.

This folder is the same material told a different way. Every chapter here covers
one arc of the project — a problem, the options that were on the table, why one
was chosen over the others, and how that choice was actually verified (not just
argued for). Nothing in these chapters is invented: every claim, every number,
every "we tried X and it didn't work" traces back to `DECISIONS.md`, the code, or
a live test that was actually run. Where this book adds something `DECISIONS.md`
doesn't, it's narrative connective tissue — *why the problem showed up when it
did*, *what it felt like to chase the bug* — not new facts.

If you want the fast reference, read `DECISIONS.md`. If you want to understand
*why* ShieldStream looks the way it does — including the parts that didn't work
the first time — read this, in order.

## How this project was built

Every chapter in this book describes a decision that was made a specific way:
state the problem, name the alternatives that were actually considered (not a
strawman), pick one, and then **verify the choice live** — against a real Redis
container, a real killed process, a real browser, a real load test — rather than
trusting that the reasoning was sound. A large fraction of the interesting
content in this book is bugs that a mocked test or a code review would never
have caught, found only because something was actually run and its output
actually read. That pattern repeats often enough that it's worth naming here,
once, instead of in every chapter: **if a chapter says "verified live," that
means someone actually did the thing and looked at the result — not "this
should work."**

## Table of Contents

**Part I — Foundations**
1. [Why ShieldStream](01-why-shieldstream.md) — the problem, the motivation, what "done" means
2. [The Shape of the System](02-the-shape-of-the-system.md) — monorepo, Docker Compose, dev/prod parity
3. [Multi-Tenancy and the Password That Wasn't a Password](03-multitenancy-and-api-keys.md) — schema design, SHA-256 vs. bcrypt
4. [Trusting the Database Instead of the Programmer](04-row-level-security.md) — Row-Level Security, and how it almost didn't work
5. [Migrations, Transactions, and a Feature That Doesn't Exist Yet](05-migrations-and-timescale.md) — Alembic, TimescaleDB, and RLS colliding

**Part II — The Gateway**
6. [The Reverse Proxy and Two Bugs Load Testing Found](06-the-reverse-proxy.md) — auth, tracing, and a downstream that couldn't keep up
7. [An Atomic Rate Limiter, Proven Not Assumed](07-the-rate-limiter-algorithm.md) — the sliding-window Lua script
8. [Wiring the Limiter In, and What Happens When Redis Dies](08-wiring-the-limiter-in.md) — fail-open, and three bugs only a real outage found

**Part III — The Streaming Pipeline**
9. [Events Off the Critical Path](09-events-off-the-critical-path.md) — Redis Streams as the producer boundary
10. [At-Least-Once, Proven by Actually Crashing the Consumer](10-the-analytics-consumer.md) — durable counting, consumer groups, kill-and-restart
11. [Two Tiers of Threat Detection](11-two-tier-threat-detection.md) — OWASP signatures and statistical anomaly scoring

**Part IV — Making It Operable**
12. [A Second Test Suite, and the Bugs Only It Could Find](12-the-e2e-suite.md) — Playwright against the real running stack
13. [Hot-Reloading Policy Without Losing the Safety Net](13-hot-reload-policy.md) — Pub/Sub on top of a TTL, not instead of it
14. [A Live Dashboard, and the Bugs That Only Showed Up in a Real Browser](14-the-live-dashboard.md) — WebSocket fan-out, Next.js, Chrome's own security model
15. [Making the Invisible Visible](15-observability.md) — metrics, tracing, structured logs, Grafana

**Part V — Under Load, and Under Fire**
16. [Three Bottlenecks the Code Review Never Would Have Found](16-load-testing.md) — Locust, 1000 users, and what actually broke
17. [Choosing a Deployment That Costs Nothing](17-the-deployment-decision.md) — walking away from $65/month of AWS
18. [Killing Redis on Purpose](18-chaos-engineering.md) — chaos testing, and the crash that wasn't the one predicted

**Part VI — Shipping**
19. [Shipping It Without Pretending](19-cicd.md) — a CI/CD pipeline honest about what it can't do yet
20. [What This Project Actually Taught](20-lessons-learned.md) — the patterns that repeated, across twelve weeks

---

*Status: covers everything built through Week 12 — the full planned scope of the
three source implementation guides. Updated as the project continues (Phase 6's
actual deployment, if and when it happens, will need its own chapter).*
