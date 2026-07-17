# Chapter 3 — Multi-Tenancy and the Password That Wasn't a Password

Every tenant of ShieldStream authenticates with an API key. That single fact —
one key, checked on every proxied request — turns out to have a surprisingly
sharp design question hiding inside it: how do you store and check that key
without either leaking it in a database breach or making every single request
slow?

## The guide's answer, and why it doesn't hold up

The original implementation guide hashes the API key with bcrypt, and — because
bcrypt's hash is salted and non-deterministic per call — checks a presented key
by scanning *every tenant's* stored hash and running bcrypt's comparison
against each one, since there's no way to build an index on a bcrypt output
that lets you look one up directly.

Bcrypt is deliberately slow. That's the entire point of bcrypt: it costs real
CPU time per guess, so an attacker trying to brute-force a *human-chosen*
password — something drawn from a small, guessable space, "password123" and
its cousins — can't just try billions of candidates a second. At a typical cost
factor of 12, one bcrypt check costs roughly 100ms.

Now put those two facts together. A cache miss on an API key lookup costs
O(n tenants × 100ms) — linear in the number of tenants, with each step costing
a tenth of a second, on a lookup that can't be indexed at all because of *how*
bcrypt works. That's not a small tax; at any real tenant count, it's the
lookup taking longer than the entire rest of the request combined.

## What bcrypt is actually for, and why an API key doesn't need it

The slow-hashing property exists to defend against brute-forcing a
*low-entropy* secret. A human password drawn from a dictionary, a common
pattern, a reused credential — these live in a search space small enough that
an attacker with a fast hash and a GPU can just try them all. Bcrypt makes
that expensive per-guess, which makes exhaustive search infeasible even though
the underlying secret is weak.

A randomly generated API key (`sk_test_<32 hex characters>`, 128 bits of
entropy) isn't a weak secret being protected by a slow hash — it's a strong
secret that doesn't need one. There's no dictionary to run through; the key
space is astronomically larger than anything brute force reaches regardless of
how fast or slow the hash function is. The *entropy itself* is the defense.
Slowing down the hash defends against a threat model (guessing a
human-memorable secret) that doesn't apply here.

This is the same reasoning production API-key schemes actually use — Stripe's,
GitHub's personal access tokens. Hash the key for at-rest protection (so a
database leak doesn't hand out live credentials directly), but use a fast,
deterministic hash, because the thing you're protecting against is a stolen
database, not a brute-force guessing attack the key's own entropy already
defeats.

## The decision

`api_key_hash = SHA-256(raw_key)`, stored under a `UNIQUE` constraint, looked
up with `WHERE api_key_hash = $1` — a single indexed equality lookup,
O(log n), the same cost whether there are ten tenants or ten thousand. SHA-256
being fast and deterministic isn't a weakness here; it's exactly what makes the
lookup cheap, and the key's own entropy is what makes the security guarantee
hold regardless.

One alternative that came up and was rejected: keep bcrypt for the stored hash,
but also store a separate lookup prefix to narrow the scan. That's strictly
worse than just using a proper indexed hash — it adds a second field, and the
lookup it enables is still approximate (a prefix match, not an exact one),
solving none of the underlying problem for the cost of more schema
complexity.

## A second, smaller gap: columns the guide's own code assumes exist

Two places in the guide's later weeks reference tenant fields that the guide's
own Week 2 schema never defines: the Week 3 proxy handler reads
`tenant['upstream_base_url']` (where does a proxied request actually get
forwarded to?), and the Week 6 event emitter reads `tenant['salt']` (used for
hashing client IPs per-tenant — see Chapter 9). Neither column exists in the
schema as written. This is the kind of gap that doesn't fail loudly during
review — it fails the first time someone actually tries to run the code the
guide describes and Python raises a `KeyError`.

Fixed by adding both columns to the initial migration: `upstream_base_url
TEXT NOT NULL`, and `ip_hash_salt TEXT NOT NULL DEFAULT
encode(gen_random_bytes(16), 'hex')` — generated automatically per tenant at
creation time, so nothing downstream ever has to remember to set it.

## Why this chapter comes before Row-Level Security

Getting authentication right — a correct, fast, defensible way to identify
which tenant is making a request — is a precondition for everything the next
chapter does. Row-Level Security enforces tenant isolation *at the database
layer*, but it can only isolate a tenant whose identity the application layer
already established correctly. Get authentication wrong, and RLS faithfully
enforces isolation for the wrong tenant.
