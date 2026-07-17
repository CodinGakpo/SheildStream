# Chapter 2 — The Shape of the System

Before any feature code, two structural questions had to be answered: how do
the pieces run together during development, and how is the code organized so
that "during development" and "in production" don't drift apart. Neither
question is glamorous. Both of them determine how much friction every single
week afterward would carry.

## The dev/prod parity problem

The Twelve-Factor App methodology names this directly as Factor X: keep
development, staging, and production as similar as possible. The failure mode
it's guarding against is familiar to anyone who's shipped software — a bug
that only exists because a developer's laptop has a different Postgres version,
a different Redis config, or services wired together with a slightly different
topology than what actually runs in production. "Works on my machine" is
usually a parity bug wearing a shrug.

Two options were on the table for local development:

- **Bare-metal local installs** of Postgres and Redis, running directly on the
  host. Fast to start, familiar to almost anyone. Rejected: it's exactly the
  parity gap Factor X warns about, and for a five-service system (gateway, two
  consumers, Postgres, Redis, plus Jaeger/Prometheus/Grafana added later) it
  gets unmanageable fast — nothing enforces that the versions, the startup
  order, or the configuration match what a real deployment would use.
- **Local Kubernetes** (Minikube or similar). This is the "enterprise-grade"
  answer, and it's wrong for the wrong reason: it solves a scaling problem
  this project doesn't have yet. A five-service stack doesn't need a
  scheduler, a control plane, and the operational overhead of learning to
  debug it — that's real cost paid for a capability nothing here was asking
  for.

**Decision:** Docker Compose, with explicit `healthcheck` blocks on every
service and `depends_on: condition: service_healthy` wired between anything
that has a real startup-order dependency (the gateway shouldn't accept traffic
before Postgres is actually ready to answer queries, not just before its
container has started). This buys the dev/prod parity Factor X asks for — the
same images, the same environment-variable-driven configuration, the same
service topology — without paying for orchestration machinery a single-host
project doesn't need. It's also the same tool the eventual hybrid deployment
target (Chapter 17) uses in production, just with a different compose file —
so "local" and "production" really do stay the same shape, not just
similar-looking.

The trade-off is close to nonexistent at this scale. A monorepo (and Docker
Compose as its local runtime) starts costing something once independent teams
need independent release cadences and independent CI pipelines per service —
none of which applies to a solo project with one contributor and one release
train.

## One repository, five directories with clear boundaries

The layout that fell out of this: `gateway/` (the FastAPI reverse proxy),
`consumers/` (two independent worker processes reading off the same event
stream), `dashboard/` (the Next.js frontend, added in Week 9), `db/` (Alembic
migrations and seed scripts), and — added much later, in Weeks 11 and 12 —
`loadtest/`, `infra/`, and `observability/`. Every directory maps to a
deployable unit with its own Dockerfile; nothing shares a runtime process with
anything else unless that sharing was itself a deliberate decision (and where
it *was* deliberate — the rate limiter running inside the gateway's own event
loop, for instance — that's argued for explicitly in the chapter where it
happens, not left implicit).

This mattered concretely the first time the analytics and alert consumers were
built as genuinely separate processes (Chapter 10, Chapter 11) rather than
background tasks inside the gateway: a slow database write in one consumer
can never compete with a live proxied request for the same event loop, because
they don't share an event loop. That property is a direct consequence of the
directory-per-deployable-unit structure decided here, in Week 0, before either
consumer existed.

## What this chapter doesn't cover

The environment quirks that showed up while getting this stack running on one
specific development machine — broken container DNS, SELinux blocking bind
mounts, a host Python version that didn't match the pinned runtime — aren't
architectural decisions so much as local friction, and they're catalogued
where they happened rather than here. They come up again, in a more serious
form, in Chapter 13, when the same SELinux bind-mount behavior that was a minor
local annoyance in Week 0 turned into a real horizontal-scaling bug in Week 9.
