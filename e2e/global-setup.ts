import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";

import { E2E_TENANTS } from "./fixtures/tenants";

const COMPOSE_FILE = "../docker-compose.yml";

function sha256Hex(raw: string): string {
  return createHash("sha256").update(raw, "utf8").digest("hex");
}

/**
 * Deletes and re-inserts the fixed tenants from fixtures/tenants.ts.
 *
 * tenants.name has no unique constraint (only api_key_hash does — see
 * db/alembic/versions/0001_initial_schema.py), so there's no ON CONFLICT
 * upsert available; delete-by-name-then-insert is the idempotent option.
 *
 * That delete is also what makes rate_limit.spec.ts deterministic across
 * reruns: tenant_id is gen_random_uuid()-default, and the Week 4 limiter's
 * key is `rate:{tenant_id}:route` (app/rate_limiter.py) — a fresh tenant_id
 * on every run means a fresh, unconsumed sliding-window key, instead of the
 * spec inheriting whatever budget a previous run already spent.
 */
function seedSql(): string {
  const tenants = Object.values(E2E_TENANTS);
  const names = tenants.map((t) => `'${t.name}'`).join(", ");
  const inserts = tenants
    .map(
      (t) => `
WITH t AS (
  INSERT INTO tenants (name, api_key_hash, upstream_base_url)
  VALUES ('${t.name}', '${sha256Hex(t.apiKey)}', 'http://httpbin:80')
  RETURNING id
)
INSERT INTO policies (tenant_id, route_pattern, rate_limit_rps, rate_limit_window_s)
SELECT id, '/proxy/*', ${t.rateLimitRps}, 60 FROM t;`,
    )
    .join("\n");
  return `DELETE FROM tenants WHERE name IN (${names});\n${inserts}\n`;
}

export default function globalSetup(): void {
  execFileSync(
    "docker",
    [
      "compose",
      "-f",
      COMPOSE_FILE,
      "exec",
      "-T",
      "postgres",
      "psql",
      "-U",
      "shieldstream",
      "-d",
      "shieldstream",
      "-v",
      "ON_ERROR_STOP=1",
      "-f",
      "-",
    ],
    { input: seedSql(), stdio: ["pipe", "inherit", "inherit"] },
  );

  // app/auth.py caches the tenant lookup in Redis for CACHE_TTL_S=30s, keyed
  // by api_key_hash — which is fixed here across runs (the raw key doesn't
  // change, only the tenant row behind it does). Without this eviction, a
  // run within 30s of a previous one would transparently serve the *old*
  // tenant_id from cache, pointing rate_limit.spec.ts at an already-spent
  // window instead of the one just seeded above.
  const cacheKeys = Object.values(E2E_TENANTS).map(
    (t) => `tenant:apikey:${sha256Hex(t.apiKey)}`,
  );
  execFileSync(
    "docker",
    ["compose", "-f", COMPOSE_FILE, "exec", "-T", "redis", "redis-cli", "DEL", ...cacheKeys],
    { stdio: "inherit" },
  );
}
