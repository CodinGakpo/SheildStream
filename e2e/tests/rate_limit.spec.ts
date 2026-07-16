import { expect, test } from "@playwright/test";

import { E2E_TENANTS } from "../fixtures/tenants";

const { apiKey: RATE_LIMIT_KEY, rateLimitRps: LIMIT } = E2E_TENANTS.ratelimit;

test("exceeding the tenant's policy returns 429 with Retry-After", async ({ request }) => {
  // global-setup reseeds this tenant (fresh tenant_id) and evicts its auth
  // cache entry before every run, so the sliding-window key
  // (`rate:{tenant_id}:/proxy/*`) always starts unconsumed here.
  for (let i = 0; i < LIMIT; i++) {
    const res = await request.get("/proxy/get", { headers: { "X-API-Key": RATE_LIMIT_KEY } });
    expect(res.status()).toBe(200);
  }

  const blocked = await request.get("/proxy/get", { headers: { "X-API-Key": RATE_LIMIT_KEY } });
  expect(blocked.status()).toBe(429);
  expect(blocked.headers()["retry-after"]).toBeDefined();
  expect(blocked.headers()["x-ratelimit-remaining"]).toBe("0");
});
