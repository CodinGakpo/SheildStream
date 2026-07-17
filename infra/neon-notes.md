# Neon Postgres compatibility notes (Week 11)

**Verified against Neon's own extension documentation (2026-07-17), correcting
an earlier assumption:** a prior planning session flagged "TimescaleDB
continuous aggregates unavailable on Neon" as an open risk to check before
committing to Neon for Phase 6. Checked now — the actual situation is better
than assumed:

- **`timescaledb` the extension is supported on Neon**, across all current
  Postgres versions, for the **Apache-2 licensed feature tier**. This
  project's schema only uses that tier: `CREATE EXTENSION timescaledb` and
  `create_hypertable('request_metrics', 'bucket')` (see
  `db/alembic/versions/0001_initial_schema.py`) are both core/Apache-2
  hypertable features, not add-ons.
- **Not supported: compression, and (separately) continuous aggregates.**
  Neither is a new gap for this project though:
  - Compression was never used here.
  - Continuous aggregates were already dropped in Phase 1 (**Revision #6**,
    DECISIONS.md) for an unrelated reason — Timescale hard-rejects a
    continuous aggregate on any RLS-enabled hypertable, full stop, on *any*
    Postgres host including a self-managed one. Continuous aggregates are
    also themselves a Timescale-licensed (not Apache-2) feature, so even
    without the RLS conflict they wouldn't be available on Neon's supported
    tier — two independent reasons landing on the same "don't use this"
    conclusion.

**Conclusion: no schema changes needed for the Neon migration.** The
existing Alembic migration should apply to a Neon branch as-is. This has
**not** been run against a live Neon database in this pass (no account
provisioned in this environment — see the "artifacts only" scope in
DECISIONS.md's Week 11 entry) — treat this as high-confidence guidance from
Neon's documentation, not a live-verified migration run. Run
`alembic upgrade head` against the real Neon connection string as the first
real test.

## Role setup on Neon

The migration already creates `shieldstream_app` (RLS-enforced,
SELECT-only) and `shieldstream_worker` (BYPASSRLS) itself, idempotently
(`CREATE ROLE ... IF NOT EXISTS`, `db/alembic/versions/0001_initial_schema.py`)
— no manual role-creation step needed. The only Neon-specific requirement is
that `alembic upgrade head` runs as a role with `CREATEROLE` privilege,
which Neon's default per-branch owner role has. Passwords are currently
hardcoded to `localdev_only` in the migration (fine for the local compose
stack, **not** fine to run as-is against Neon) — change those two
`CREATE ROLE ... PASSWORD` literals to real generated secrets before running
the migration against the production Neon branch, and store the resulting
`DATABASE_URL`/`ADMIN_DATABASE_URL` in the VM's `.env.prod` (see
`infra/.env.prod.example`), never in git.
