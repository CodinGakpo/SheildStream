import { expect, test } from "@playwright/test";
import { E2E_TENANTS } from "../fixtures/tenants";

// Week 9 Part B: the gateway's /ws/dashboard fans out two Redis Pub/Sub
// channels — dashboard:alerts (Week 8 alert consumer) and dashboard:metrics
// (this week's per-second RPS snapshot, published by the alert consumer,
// see consumers/alerts/worker.py). This is the first spec in this suite
// that needs a real browser: Playwright's `request` fixture is HTTP-only
// and has no WebSocket support, so these use `page.evaluate` purely as a
// WebSocket client harness — no dashboard UI is involved or required.
const WS_URL = process.env.E2E_WS_URL ?? "ws://localhost:8000/ws/dashboard";

test("dashboard WS receives periodic METRIC_SNAPSHOT frames", async ({ page }) => {
  await page.goto("about:blank");
  const messages = await page.evaluate(
    (wsUrl) =>
      new Promise<string[]>((resolve, reject) => {
        const socket = new WebSocket(wsUrl);
        const received: string[] = [];
        socket.onmessage = (evt) => received.push(evt.data);
        socket.onerror = () => reject(new Error("websocket error"));
        setTimeout(() => {
          socket.close();
          resolve(received);
        }, 3000);
      }),
    WS_URL,
  );

  const snapshots = messages.map((m) => JSON.parse(m)).filter((m) => m.type === "METRIC_SNAPSHOT");
  // Published roughly once per second by the alert consumer's loop tick —
  // a 3s window should reliably catch at least one, usually two or three.
  expect(snapshots.length).toBeGreaterThan(0);
});

test("an injected SQLi request produces a THREAT_DETECTED alert on the WS within ~2s", async ({
  page,
  request,
}) => {
  await page.goto("about:blank");
  await page.evaluate(
    (wsUrl) =>
      new Promise<void>((resolve, reject) => {
        const socket = new WebSocket(wsUrl);
        (window as unknown as { __wsMessages: string[] }).__wsMessages = [];
        socket.onopen = () => resolve();
        socket.onmessage = (evt) =>
          (window as unknown as { __wsMessages: string[] }).__wsMessages.push(evt.data);
        socket.onerror = () => reject(new Error("websocket error"));
      }),
    WS_URL,
  );

  await request.get("/proxy/get", {
    params: { q: "' OR 1=1--" },
    headers: { "X-API-Key": E2E_TENANTS.auth.apiKey },
  });

  await page.waitForTimeout(2000);
  const raw = await page.evaluate(
    () => (window as unknown as { __wsMessages: string[] }).__wsMessages,
  );
  const alerts = raw.map((m) => JSON.parse(m)).filter((m) => m.type === "THREAT_DETECTED");
  expect(alerts.length).toBeGreaterThan(0);
});
