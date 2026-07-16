import { defineConfig } from "@playwright/test";

// Pure API testing (the `request` fixture, no browser contexts) — see
// README.md for why this suite exists alongside gateway/tests' pytest suite.
export default defineConfig({
  testDir: "./tests",
  // The rate-limit spec deliberately exhausts its tenant's window; running
  // specs in parallel workers would race that against unrelated assertions.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  globalSetup: require.resolve("./global-setup"),
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
  },
});
