# Deploying ShieldStream (hybrid free-tier)

This is the prepared deployment path chosen instead of the implementation
guide's ~$65/month AWS ECS/CloudFormation plan (see DECISIONS.md's Week 11
entry for why). Everything in this file and this directory is **prepared,
not provisioned** — no cloud accounts exist for this project in the
environment these artifacts were written in, so treat this as a runbook to
execute yourself, not a record of a deployment that already happened.

**Target cost: ~$0/month.** One always-free compute instance running most of
the stack, a free-tier managed Postgres, and a free frontend host.

## Components and where they run

| Component | Where | Why |
|---|---|---|
| gateway, analytics-consumer, alert-consumer, redis, prometheus, grafana, jaeger, caddy | One always-free VM (Oracle Cloud Free Tier `VM.Standard.A1.Flex`, or AWS free-tier/credits `t3.micro`/`t4g.small`) | Docker Compose, same shape as local dev minus Postgres/httpbin — see `docker-compose.prod.yml` |
| Postgres (`tenants`, `policies`, `request_metrics`, `audit_events`) | Neon free tier | Managed backups/branching for ~free; verified TimescaleDB-extension-compatible — see `neon-notes.md` |
| Dashboard (Next.js) | Vercel free tier | Zero-config Next.js hosting; the dashboard is a pure client of the gateway's `/ws/dashboard`, no server-side coupling to the VM |
| Container images | GHCR (`ghcr.io/<owner>/shieldstream-*`) | Free for public repos; Week 12's CI/CD publishes here |

## One-time setup

1. **VM**: provision the free-tier instance, install Docker + Compose
   plugin, point a domain's DNS (A record) at its IP. Two subdomains needed
   (`api.example.com`, `grafana.example.com` in the example `Caddyfile` —
   edit to match your real domain).
2. **Neon**: create a project, get the branch's connection string (this is
   the role with `CREATEROLE` — do **not** use it as `DATABASE_URL`
   directly). Change the two hardcoded `PASSWORD 'localdev_only'` literals
   in `db/alembic/versions/0001_initial_schema.py` to real secrets, then run
   `alembic upgrade head` against the Neon connection string from a machine
   with the repo checked out (this creates `shieldstream_app` /
   `shieldstream_worker` and grants — see `neon-notes.md`). Record the two
   roles' connection strings for `.env.prod`.
3. **GHCR**: Week 12's GitHub Actions workflow pushes images here on merge;
   until that exists, build and push manually once:
   ```
   docker build -t ghcr.io/<owner>/shieldstream-gateway:latest ./gateway
   docker push ghcr.io/<owner>/shieldstream-gateway:latest
   # repeat for ./consumers (two images: analytics.worker, alerts.worker
   # are the same image, different `command:` — see docker-compose.prod.yml)
   ```
4. **VM**: copy `infra/` to the VM (or clone the whole repo), copy
   `.env.prod.example` to `.env.prod` and fill in the Neon URLs + a real
   Grafana admin password, edit `Caddyfile`'s two domains, then:
   ```
   docker compose -f docker-compose.prod.yml --env-file .env.prod up -d
   ```
   Caddy requests its certs on first real request to each domain — no
   separate cert step.
5. **Vercel**: import the `dashboard/` directory as a Vercel project (Next.js
   auto-detected, no `vercel.json` needed). Set one environment variable:
   `NEXT_PUBLIC_WS_URL=wss://api.example.com/ws/dashboard` (note `wss://`,
   not `ws://` — Caddy terminates TLS, so the dashboard's browser-side
   WebSocket must use the secure scheme once it's not `localhost` anymore;
   this is exactly the Chrome LNA / cross-origin situation flagged in
   DECISIONS.md's Week 9 entry, now actually happening for real instead of
   just being anticipated).
6. **Tenants**: seed real tenants against the Neon database (`db/seed.py`
   with `MIGRATION_DATABASE_URL` pointed at Neon) — the local dev/e2e fixed
   test tenants should never be seeded into production.

## What's intentionally NOT here

- A CI/CD pipeline that does steps 3 onward automatically — that's Week 12's
  GitHub Actions work, layered on top of this once it exists.
- TLS certificate automation beyond Caddy's own built-in ACME client — no
  separate cert-manager needed at this scale.
- Postgres HA / read replicas, Redis HA, or multi-VM redundancy — out of
  scope for a ~$0/month portfolio deployment; documented as an accepted
  trade-off, not an oversight (see also Week 11's load-test single-core CPU
  ceiling finding in DECISIONS.md, and Week 9's already-proven horizontal
  gateway scaling as the actual production answer if this ever needed to
  handle real load).
