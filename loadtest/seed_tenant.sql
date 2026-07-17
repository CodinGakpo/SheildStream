-- Idempotent seed for the Week 11 load-test tenant. Delete-then-insert, same
-- pattern as e2e/global-setup.ts (see DECISIONS.md, "Tooling — Playwright
-- E2E suite") — tenants.name has no unique constraint, only api_key_hash
-- does, and a fresh gen_random_uuid() tenant_id on every run also resets the
-- rate limiter's sliding window (keyed by tenant_id), which matters here
-- for a clean, repeatable run.
--
-- rate_limit_rps is a request COUNT over the window, not a true per-second
-- rate (see gateway/app/lua/sliding_window.lua's own comment: "limit = max
-- requests allowed inside the window") — the field name is a Week 4
-- holdover from when window_s was always 1. A first pass here set
-- rate_limit_rps=5000 with the guide-default window_s=60, which is actually
-- a ~83 req/s sustained cap (5000/60s), not 5000 req/s — caught live when
-- the Week 11 load test's failure breakdown showed real 429s mixed in with
-- the connection-pool errors it was meant to isolate. window_s=1 here makes
-- the field's name and its actual behavior match.
-- The api key is fixed (not random, unlike db/seed.py) so locustfile.py
-- doesn't need runtime coordination with this script.
DELETE FROM tenants WHERE name = 'loadtest';

INSERT INTO tenants (name, api_key_hash, upstream_base_url)
VALUES (
    'loadtest',
    '353fed0e6ab4ec7784f4c4829e4bc585011946531c6d945ed8297ccaafddfcc9', -- sha256("sk_test_loadtest_fixedkey_localdev")
    'http://httpbin:80'
)
RETURNING id \gset

INSERT INTO policies (tenant_id, route_pattern, rate_limit_rps, rate_limit_window_s)
VALUES (:'id', '/proxy/*', 5000, 1);
