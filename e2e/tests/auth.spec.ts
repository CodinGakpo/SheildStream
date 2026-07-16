import { expect, test } from "@playwright/test";

import { E2E_TENANTS } from "../fixtures/tenants";

const AUTH_KEY = E2E_TENANTS.auth.apiKey;

test.describe("gateway auth", () => {
  test("missing X-API-Key fails FastAPI's required-header validation, not app auth", async ({
    request,
  }) => {
    const res = await request.get("/proxy/get");
    expect(res.status()).toBe(422);
  });

  test("invalid X-API-Key is rejected by app.auth.get_tenant", async ({ request }) => {
    const res = await request.get("/proxy/get", {
      headers: { "X-API-Key": "sk_test_definitely_not_a_real_key" },
    });
    expect(res.status()).toBe(401);
  });

  test("valid X-API-Key authenticates and proxies through", async ({ request }) => {
    const res = await request.get("/proxy/get", {
      headers: { "X-API-Key": AUTH_KEY },
    });
    expect(res.status()).toBe(200);
    // Set on every successful response by app/routes/proxy.py so a
    // well-behaved client can self-throttle before hitting 429.
    expect(res.headers()["x-ratelimit-limit"]).toBe(String(E2E_TENANTS.auth.rateLimitRps));
  });
});
