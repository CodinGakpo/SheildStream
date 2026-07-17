import { expect, test } from "@playwright/test";
import { E2E_TENANTS } from "../fixtures/tenants";

// Week 9 Part C: now that the real Next.js dashboard exists (dashboard/),
// these specs drive the actual UI instead of a raw WebSocket harness — per
// this file's own prior comment ("changes once the Week 9 dashboard exists
// and gets browser specs here"). The dashboard connects to the gateway's
// /ws/dashboard fan-out, which multiplexes dashboard:alerts and
// dashboard:metrics (see consumers/alerts/worker.py).
const DASHBOARD_URL = process.env.E2E_DASHBOARD_URL ?? "http://localhost:3000";

test("dashboard connects and the RPS chart populates from METRIC_SNAPSHOT frames", async ({
  page,
}) => {
  await page.goto(DASHBOARD_URL);

  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "open", {
    timeout: 5000,
  });

  // Published roughly once per second by the alert consumer's loop tick —
  // the "waiting for first snapshot" placeholder should be gone well within
  // a few seconds of connecting.
  await expect(page.getByText("Waiting for first metric snapshot")).toHaveCount(0, {
    timeout: 5000,
  });
});

test("an injected SQLi request produces a HIGH-severity alert in the threat feed within ~2s", async ({
  page,
  request,
}) => {
  await page.goto(DASHBOARD_URL);
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "open", {
    timeout: 5000,
  });

  await request.get("http://localhost:8000/proxy/get", {
    params: { q: "' OR 1=1--" },
    headers: { "X-API-Key": E2E_TENANTS.auth.apiKey },
  });

  const feedItem = page.getByText(/SQLI on/i).first();
  await expect(feedItem).toBeVisible({ timeout: 3000 });

  const container = page.locator("li", { has: feedItem });
  await expect(container.getByText("HIGH")).toBeVisible();
});

test("gateway restart: dashboard shows reconnecting then recovers without a page reload", async ({
  page,
}) => {
  test.setTimeout(30_000);
  await page.goto(DASHBOARD_URL);
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "open", {
    timeout: 5000,
  });

  const { execSync } = require("child_process");
  execSync("docker compose restart gateway", { cwd: "../" });

  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "reconnecting", {
    timeout: 10_000,
  });
  await expect(page.getByTestId("connection-status")).toHaveAttribute("data-status", "open", {
    timeout: 20_000,
  });
});
