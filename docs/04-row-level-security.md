# Chapter 4 — Trusting the Database Instead of the Programmer

Once tenant identity is established (Chapter 3), the next question is how to
guarantee that one tenant's code path can never accidentally read or write
another tenant's data. The obvious answer — every query includes
`WHERE tenant_id = ?` — is also the wrong one to rely on exclusively, for a
reason that has nothing to do with skill and everything to do with what
happens at scale: it works exactly as long as *every engineer, on *every*
query, forever* remembers to add the clause. One missed `WHERE`, in one query,
in one endpoint added eighteen months from now by someone who's never read this
paragraph, and tenant isolation is gone — silently, with no error, no crash,
just the wrong rows in a response.

## Row-Level Security: moving the guarantee into the database

PostgreSQL's Row-Level Security (RLS) lets you attach a policy to a table that
the database itself enforces on every query, regardless of what the
application code asked for. Enable it correctly, and a `SELECT * FROM
request_metrics` issued by an application connection literally cannot return
rows belonging to a tenant other than the one that connection is scoped to —
not because the query happened to filter correctly, but because the database
refuses to hand back rows the policy excludes. This moves the isolation
guarantee out of "a discipline every engineer must maintain forever" and into
"a property the database enforces structurally." That's a categorically
stronger guarantee, and it's why RLS was chosen over relying on
application-layer filtering alone.

## The gap that would have made it silently inert

Here's the part that doesn't show up until you actually try to break it: RLS
is inert — completely inert, as if it were never enabled — for the table's
owning role, and for any role with the `BYPASSRLS` attribute, regardless of
what policies are defined. This is documented Postgres behavior, and the
implementation guide's own pitfall notes for this week even mention it in
passing ("connecting as a Postgres superuser... bypasses RLS entirely"). But
the guide never actually acts on its own warning: it never introduces a
non-owner application role anywhere in its schema or its testing
instructions. Every example query, every verification step, connects as the
migration owner — which means the guide's own RLS test would pass whether or
not RLS was doing anything at all. A completely broken RLS policy and a
correctly working one look identical from inside the guide's own verification
process.

That's a genuinely dangerous kind of bug: not a crash, not a visible symptom,
just a security guarantee that was never actually being enforced, discovered
only by someone who thinks to test it from the *governed* role's perspective
instead of the *owning* role's.

## The decision: two roles, one governed, one trusted

Two non-owner database roles were created in the same migration that defines
the schema:

- **`shieldstream_app`** — the gateway's own runtime identity. RLS-bound,
  granted only `SELECT` on `tenants`/`policies` and
  `SELECT`/`INSERT`/`UPDATE` on `request_metrics`. This is the role every
  tenant-facing request actually runs as, and it's the role the RLS
  verification test connects as — the role the guarantee is actually meant to
  protect.
- **`shieldstream_worker`** — used by the analytics and alert consumers, and
  later the admin policy API. Granted `BYPASSRLS`, deliberately, because these
  are trusted internal processes that need to batch-write rows spanning many
  tenants in a single transaction (the analytics consumer's micro-batched
  upsert, built in Chapter 10, is structurally incompatible with a single
  session-scoped tenant context — it's writing for dozens of tenants in one
  statement).

`request_metrics` additionally gets `FORCE ROW LEVEL SECURITY`, not just
`ENABLE` — the distinction matters because plain `ENABLE` still exempts the
table owner, and `FORCE` closes that gap too, so the policy genuinely applies
to every role except the ones explicitly granted `BYPASSRLS`.

## What this trades away, honestly

`shieldstream_worker`'s `BYPASSRLS` means a bug in consumer code *could*, in
principle, cross-write another tenant's rows without the database catching
it — the RLS guarantee is scoped to `shieldstream_app`'s query path (the
tenant-facing surface, present and future), not to the trusted internal write
path. This isn't a gap that was missed; it's a deliberate boundary, and the
control that actually protects `request_metrics` on the worker side isn't RLS
at all — it's that there's exactly one process (the analytics consumer)
that ever writes to that table, and its logic is small, reviewed, and covered
by the kill-and-restart tests in Chapter 10. A single, audited writer is doing
the real work here; RLS is protecting the surface where an unaudited amount
of future code — every tenant-facing endpoint that will ever be added — runs.

## Verified, not just argued

The test that actually matters: connect as `shieldstream_app` with
`app.tenant_id` set to one tenant's ID, and run a bare `SELECT * FROM
request_metrics` with no `WHERE` clause at all. A row belonging to a *different*
tenant, inserted moments earlier in the same test, has to be invisible — not
filtered by the query, invisible because the database won't return it. That's
what was run, and that's what happened: the second tenant's row simply isn't
in the result set. Connect as `shieldstream_worker` instead, run the identical
query, and both tenants' rows come back — confirming `BYPASSRLS` works exactly
as designed for the one path that's supposed to see everything.

The difference between this and the guide's own verification step is the
whole point of the chapter: testing from the role that's supposed to be
*governed* by the policy, not from the role that was creating the schema in
the first place.
