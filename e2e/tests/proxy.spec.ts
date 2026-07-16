import { expect, test } from "@playwright/test";

import { E2E_TENANTS } from "../fixtures/tenants";

const AUTH_KEY = E2E_TENANTS.auth.apiKey;

test.describe("proxy passthrough", () => {
  test("forwards to the tenant's upstream and preserves query params", async ({ request }) => {
    const res = await request.get("/proxy/get?probe=shieldstream", {
      headers: { "X-API-Key": AUTH_KEY },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.args).toEqual({ probe: "shieldstream" });
  });

  test("strips the X-API-Key credential before forwarding upstream", async ({ request }) => {
    // app/routes/proxy.py's _STRIP_INBOUND set — the downstream service has
    // no use for ShieldStream's credential and shouldn't see it.
    const res = await request.get("/proxy/headers", {
      headers: { "X-API-Key": AUTH_KEY },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    const forwardedNames = Object.keys(body.headers).map((h) => h.toLowerCase());
    expect(forwardedNames).not.toContain("x-api-key");
  });
});
