# Chapter 5 — Migrations, Transactions, and a Feature That Doesn't Exist Yet

The schema work in Weeks 1 and 2 ran into two separate collisions with how
Alembic and TimescaleDB actually behave under the hood — the kind of thing
that's invisible reading either tool's documentation in isolation, and only
shows up when a migration is actually run.

## Alembic wraps everything in a transaction. Some things refuse to run inside one.

Alembic, by default, wraps every migration in a single transaction — sensible
for the common case (an atomic all-or-nothing schema change), and a problem
for two specific operations this schema needed: `CREATE INDEX CONCURRENTLY`
and TimescaleDB's `CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous)`.
Both refuse to run inside an explicit transaction block — Postgres and
TimescaleDB enforce this, not as a style preference but as a hard restriction,
because both operations have internal locking behavior that's fundamentally
incompatible with being wrapped in an outer transaction someone else controls.

For the index, the fix was to just not use `CONCURRENTLY` — the entire reason
that variant exists is to avoid locking a *live* table with real traffic while
the index builds; this was the very first migration, against a table that had
just been created and had no rows and no concurrent readers. There was no live
table to protect, so the concurrency-safe (and transaction-hostile) variant
wasn't buying anything here. A plain `CREATE INDEX` works fine inside
Alembic's managed transaction.

For the continuous aggregate, the fix was more surgical: an explicit `COMMIT`
issued right before the `CREATE MATERIALIZED VIEW` statement, and a `BEGIN`
issued right after, so the Timescale-specific statement runs entirely outside
Alembic's managed transaction while leaving an empty transaction open for
Alembic's own trailing commit to close without incident. It's an inelegant
workaround, and it's flagged as one in the code and in the decision log — any
future migration that touches another `CONCURRENTLY`-style operation on a live
table will need the same pattern applied deliberately again, not assumed to
just work because it worked once.

## Then the continuous aggregate turned out not to exist at all

Running that migration for the first time surfaced a second, unrelated
failure: `psycopg2.errors.FeatureNotSupported: cannot create continuous
aggregate on hypertable with row security`. This isn't a transaction-scoping
problem — the autocommit fix above doesn't touch it. It's a hard TimescaleDB
restriction with a genuinely sound reason behind it: a continuous aggregate's
incremental refresh runs as a background worker, entirely outside any request
or session context. Row-Level Security policies (Chapter 4) are evaluated
against session-scoped settings like `current_setting('app.tenant_id')` — and
a background worker has no session, no tenant context, nothing for the policy
to evaluate against. Rather than silently materializing rows across every
tenant unfiltered (which is exactly the failure RLS exists to prevent),
TimescaleDB refuses to create the aggregate at all.

This is a genuine conflict between two things this project wanted at once:
automatic, incrementally-refreshed rollups (TimescaleDB's continuous
aggregates), and database-enforced multi-tenant isolation (RLS, Chapter 4).
They can't both apply to the same table.

**Two ways out were considered and rejected.** Drop RLS from
`request_metrics` to unblock the aggregate — rejected outright, because that
undoes the one structural guarantee Chapter 4 exists to provide, on exactly
the table where cross-tenant leakage would matter most (it's the table that
holds every tenant's traffic history). Create the aggregate on a separate,
RLS-free shadow table fed by a trigger — rejected because it adds a second
write path and a trigger-maintenance surface to solve a problem the analytics
consumer's own upsert (Chapter 10) already solves more simply once it exists.

**The decision:** `request_metrics_hourly` is a plain table, not a continuous
aggregate — carrying the same RLS treatment as `request_metrics` (`ENABLE`
and `FORCE ROW LEVEL SECURITY`, the same isolation policy), populated by an
explicit `INSERT ... ON CONFLICT DO UPDATE` upsert that the analytics
consumer (already the sole, `BYPASSRLS`-privileged writer to `request_metrics`,
Chapter 4) runs periodically. It didn't need to be built in Week 2 at all,
since nothing reads it before the Week 9 dashboard exists — so it was deferred
and built alongside that feature instead of speculatively ahead of any
consumer.

**What this trades away:** TimescaleDB's continuous aggregates recompute only
what changed since the last refresh — a genuinely more efficient incremental
model than a periodic full-window upsert. At this project's data volume, that
difference is immaterial. And there's a second, larger point buried in this
trade-off: continuous aggregates are a Timescale-licensed feature, not part of
the open Apache-2 tier — meaning they wouldn't have been available on Neon,
the managed Postgres provider chosen for the eventual deployment (Chapter 17),
regardless of the RLS conflict. This decision arrived at the same destination
the deployment plan was always going to need anyway, just one phase earlier
than strictly necessary.

## The pattern, again

Neither of these bugs is something a documentation page states outright.
`CREATE INDEX CONCURRENTLY`'s transaction restriction is discoverable by
reading Postgres's docs carefully. The RLS/continuous-aggregate conflict
isn't documented anywhere obvious at all — it was found by running the
migration exactly as written and reading the actual error Postgres raised.
That's the throughline connecting this chapter to the last one, and to most of
what follows: the guide's pseudocode described a shape that looked plausible
on the page, and the gap between "looks plausible" and "actually runs" kept
turning out to be where the real engineering work lived.
