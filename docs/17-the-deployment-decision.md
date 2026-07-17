# Chapter 17 — Choosing a Deployment That Costs Nothing

The original implementation guides — and the project's own initial
blueprint — specify an entirely AWS-native production deployment:
CloudFormation, ECS Fargate for compute, RDS for Postgres, ElastiCache for
Redis, an Application Load Balancer in front of it all. It's a coherent,
defensible architecture, and it costs a real, ongoing amount of money —
roughly $65 a month, by the original plan's own estimate. For a portfolio
project, that's a real cost with no revenue behind it, running indefinitely
for as long as the deployment stays up.

## The decision, made once and then almost lost

At an earlier point in this project, a different path was chosen: a hybrid,
effectively free-tier deployment — one always-free compute instance running
most of the stack under Docker Compose, a managed free-tier Postgres provider
(Neon) instead of RDS, Vercel for the dashboard's static hosting instead of
running Next.js on the same VM, and Caddy as a lightweight reverse proxy
in place of an Application Load Balancer. That decision was real and
deliberate — and it existed, for a while, only as a passing note in a memory
file, never actually written into this project's own decision record. This
chapter is that formalization: the first place this decision becomes a real,
checked-in artifact rather than something remembered informally.

**No cloud accounts exist in the environment this deployment plan was
written in.** Everything under `infra/` — the production Compose file, the
Caddy configuration, the deployment runbook, the environment-variable
template — is *prepared*, not *provisioned*. None of it has been run against
a real VM, a real Neon database branch, or a real Vercel project. The
runbook (`infra/DEPLOY.md`) is written to be executed by a human with real
credentials, not a record of something already completed. That distinction
is stated plainly rather than blurred, because the difference between
"this configuration is correct" and "this configuration has actually been
run successfully" is exactly the kind of gap this project has spent sixteen
chapters insisting on closing through live verification wherever it's
actually possible to do so.

## An assumption that turned out to be wrong, caught by checking instead of carrying it forward

The original decision to move away from AWS came with one flagged, unresolved
risk: whether Neon — the chosen managed Postgres provider — actually supports
the TimescaleDB extension this project's schema depends on (`request_metrics`
is a real hypertable, built back in Chapter 5). That risk sat unresolved
until this chapter, when it was actually checked against Neon's own current
documentation rather than continuing to be carried forward as an open
question.

The answer turned out to be better than the open risk implied: Neon *does*
support the `timescaledb` extension, across every currently supported
Postgres version, for the Apache-2 licensed tier of Timescale's
functionality. And this project's actual schema only ever uses that tier —
`create_hypertable()` for chunking, nothing more exotic. The one feature that
genuinely isn't available (continuous aggregates) was already abandoned back
in Chapter 5, for an entirely unrelated reason — it's structurally
incompatible with Row-Level Security, regardless of which Postgres host is
running it. As it happens, continuous aggregates are *also* a
Timescale-licensed feature, unavailable on Neon's supported tier
independently of the RLS conflict — meaning Chapter 5's decision, made for a
completely different reason, happened to already avoid the one gap that
would have mattered here. Net effect: the migration to Neon needs no schema
changes at all, a cleaner outcome than the flagged risk suggested, found by
actually checking rather than assuming the risk was still open.

## Why Caddy, specifically

Caddy replaces the Application Load Balancer for the same underlying reason
several earlier decisions in this project replaced a heavier default with a
simpler tool that does the one thing actually needed: automatic HTTPS
certificate provisioning and renewal, reverse proxying, and transparent
WebSocket upgrade handling — all built in, all free, with none of the
AWS-specific machinery (ACM, Route 53 integration, target-group health-check
configuration) an ALB assumes exists around it. Grafana, Prometheus, and
Jaeger — all of Chapter 15's observability stack — stay self-hosted on the
same VM rather than migrating to a managed observability SaaS; they're
already free to run, and there's no clear benefit to paying for a hosted
equivalent of something already working. Only two things actually move off
the VM: Postgres, because a managed provider's backup and durability story is
genuinely stronger than anything worth self-managing at this scale, and the
dashboard, because static Next.js hosting is close to free and removes an
entire deployment target's worth of operational surface from the VM
entirely.

## What this chapter is, and isn't

This is a decision record, and a set of prepared artifacts — not a
completed deployment. The next real step, whenever it happens, is running
`infra/DEPLOY.md` against actual accounts and reporting back what actually
happened when it was tried, the same way every other chapter in this book
reports what actually happened rather than what was expected to happen. Until
that step happens, this chapter's claims are scoped honestly: the plan is
sound, the one open technical risk it carried has been checked and resolved,
and none of it has been proven by running it yet.
