import { defineConfig } from "@playwright/test";

// Mostly pure API testing (the `request` fixture, no browser needed) — see
// README.md for why this suite exists alongside gateway/tests' pytest suite.
// Week 9 added the first browser-necessitating specs (dashboard_ws.spec.ts):
// Playwright's `request` fixture is HTTP-only, so verifying the gateway's
// WebSocket fan-out needs a real `page` as a WebSocket client harness.
export default defineConfig({
  testDir: "./tests",
  // The rate-limit spec deliberately exhausts its tenant's window; running
  // specs in parallel workers would race that against unrelated assertions.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: require.resolve("./global-setup"),
  // Week 9's dashboard specs need the Next.js dev server running against the
  // same compose stack the other specs already assume is up. `reuseExisting
  // Server: true` means a manually-started `npm run dev` (common during
  // active dashboard development) is left alone rather than double-started.
  webServer: {
    command: "npm run dev",
    cwd: "../dashboard",
    url: "http://localhost:3000",
    reuseExistingServer: true,
    timeout: 30_000,
  },
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
    // Chrome's Local Network Access checks (a page's origin connecting out to
    // a "more local" address, e.g. any browser page -> ws://localhost:8000)
    // block the connection outright by default — discovered live, running
    // dashboard_ws.spec.ts the first time (net::ERR_BLOCKED_BY_LOCAL_NETWORK_
    // ACCESS_CHECKS). This isn't a test-only quirk: the real Next.js
    // dashboard (its own origin, e.g. localhost:3000) connecting to the
    // gateway's ws://localhost:8000 is the exact same cross-origin,
    // local-network pattern, so it will hit this in a real browser too.
    // Disabling the check here is scoped to this test harness's Chromium
    // only and has no bearing on anything served in production, where the
    // dashboard and gateway sit behind real, non-localhost hostnames.
    launchOptions: {
      args: ["--disable-features=LocalNetworkAccessChecks,PrivateNetworkAccessChecks"],
    },
  },
});
