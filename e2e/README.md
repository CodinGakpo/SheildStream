# E2E (Playwright, API testing)

Black-box tests against the *real* running gateway over HTTP — real network
stack, real Redis/Postgres, real uvicorn response headers. This is
deliberately different from `gateway/tests` (pytest): those use
`httpx.ASGITransport` to call the FastAPI app in-process, which is faster and
fine for exercising app logic, but never actually goes over a socket — it
can't catch things like a header the ASGI layer adds/drops only on a real
HTTP response, or (later, Week 9+) a WebSocket handshake. This suite trades
that speed for being a true consumer of the deployed stack.

Most specs use Playwright's `request` fixture (API testing only, no browser).
`dashboard_ws.spec.ts` is the exception (added Week 9): it drives a real
Chromium `page` against the actual Next.js dashboard (`dashboard/`) as a
true WebSocket client, since `request` has no WebSocket support.

## Prerequisites

The full compose stack must be up (`docker compose up -d` from the repo
root) — `gateway`, `postgres`, `redis`, and `httpbin` are all live-tested
against. The dashboard's dev server is started automatically by
`playwright.config.ts`'s `webServer` (reuses one you already have running
via `npm run dev` in `dashboard/`, or starts one).

## Run

```bash
npm install
npx playwright test
```

`global-setup.ts` seeds two fixed, idempotent test tenants (`e2e-auth`,
`e2e-ratelimit` — see `fixtures/tenants.ts`) straight into the compose
stack's Postgres before every run, and evicts their auth-cache entries in
Redis. Nothing here touches `db/seed.py`'s tenants (`acme-corp`,
`globex-inc`) or their randomly-generated keys.
