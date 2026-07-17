# Chapter 20 — What This Project Actually Taught

Nineteen chapters, twelve weeks, one system. Read back to back, the same
handful of patterns keep showing up in genuinely different contexts — not
because they were imposed as a methodology from the outset, but because they
kept being the thing that actually worked, independently rediscovered every
few weeks in a new part of the codebase. Naming them here, once, together, is
more useful than leaving them scattered as an implicit habit visible only to
someone who reads all twenty chapters in a row.

## "Verify live" isn't a slogan — it's where almost every real bug was found

Count the bugs across this book that a mocked unit test, or a careful code
review, would have caught on its own: a small number. Now count the ones
that only became visible by actually running something and reading what it
actually did — the eager database dependency that only cost anything under
real concurrency (Chapter 6), the three fail-open defects that only existed
once Redis was genuinely, physically killed rather than mocked into raising
an exception (Chapter 8), the crash-recovery design that looks correct on
the page and loses data the instant it's actually killed mid-batch
(Chapter 10), the Grafana alert expression that only reveals its mistake
when Grafana itself actually tries to evaluate it (Chapter 15). That's most
of the book. The guide's own pitfall notes said this explicitly, more than
once — "don't test fail-open by mocking the exception, kill the container" —
and every time that advice was followed to the letter, it found something
real. The lesson generalizes past this specific project: reasoning about
correctness from the code alone is necessary, but it is not sufficient, and
the gap between "this should work" and "this does work" is exactly where
production incidents come from.

## Honest numbers are more useful than good-looking numbers

Chapter 6's proxy-overhead measurement discarded its first attempt because
the status-code distribution had silently skewed the result. Chapter 9's
latency claim did the same thing. Chapter 16 reported p50 and p99 latency
numbers that missed the guide's target by three orders of magnitude,
explained *why*, and moved on rather than re-running the test under
conditions engineered to produce a better-looking number. Chapter 18
reported an experiment that didn't work. None of this is an accident of
style — it's a deliberate practice, applied consistently enough across
twenty chapters that it stopped being a choice and became simply how work
got reported. A number that's been checked for what might be quietly wrong
with it is worth more than a number that merely looks good, and a project
whose documentation only ever reports successes teaches its reader nothing
about how the failures were actually found and fixed — which, as this book's
own table of contents shows, is where most of the real engineering content
lives.

## Guides are a starting point, not a specification

Something close to a dozen distinct, documented bugs in the source
implementation guides' own pseudocode surface across this book — wrong
imports, undefined functions, a race condition in the guide's own suggested
middleware ordering, a cache key that would never match anything, a database
query missing a `WHERE` clause that amounts to a real security
vulnerability, crash-recovery logic that loses data on the very first
restart it's tested against. None of these are typos. They're the specific
kind of mistake that pseudocode makes and gets away with, because pseudocode
never actually runs, never actually gets killed mid-flight, never actually
gets hit by two concurrent requests at once. The guides were still valuable —
genuinely, not as a hedge — they set direction, named the right problems to
solve in the right order, and got most of the *shape* of each solution
right. But "the guide says so" was never treated as a stopping point for
verification, and roughly a dozen real bugs is the return on treating it that
way instead.

## Simplicity, chosen deliberately, over the more impressive-sounding option

SHA-256 over bcrypt for API keys (Chapter 3), because the threat model
bcrypt defends against doesn't apply to a high-entropy secret. A single
atomic Lua script over a more "distributed-systems-flavored" coordination
scheme for the rate limiter (Chapter 7), because atomicity by construction
beats atomicity by careful discipline. Caddy over an Application Load
Balancer (Chapter 17), because automatic HTTPS and a reverse proxy is all
that was actually needed, and everything an ALB adds on top of that was
AWS-specific machinery this deployment target doesn't have a use for. In
every one of these cases, the simpler option wasn't chosen because it was
easier to build — several of them took *more* investigation to arrive at
than the default, more familiar choice would have. It was chosen because it
was the smallest thing that actually solved the problem in front of it,
with the reasoning for why the more elaborate alternative wasn't needed
written down at the time, not reconstructed after the fact to sound more
deliberate than it was.

## Fail-open, applied consistently, as an actual design philosophy

Redis being unreachable degrades the rate limiter to an in-memory fallback
(Chapter 8), degrades authentication and policy lookups to hitting Postgres
directly (Chapter 8), and never once takes the whole gateway down. Policy
hot-reload's Pub/Sub push (Chapter 13) never replaces its own TTL fallback —
it only ever adds a faster path on top of a slower path that was already
correct on its own. A crashed consumer process recovers its own pending work
automatically on restart, with zero coordination required (Chapter 10). This
shows up independently in at least four different subsystems across the
project, and it's the same underlying design instinct every time: assume the
dependency you're relying on will, eventually, fail — and design the
*primary* path so that failure degrades gracefully into a slower, still-
correct path, rather than treating graceful degradation as an afterthought
bolted onto a design that assumed the happy path was the only path.

## What's actually left

This book covers everything built through Week 12 — the full scope of the
three source implementation guides, load-tested, chaos-tested, and shipped
with a real (if honestly partial) CI/CD pipeline. What isn't done yet is
Chapter 17's deployment plan actually being *run* — the artifacts are
prepared, the one open technical risk in the plan has been checked and
resolved, but no VM has actually been provisioned, no Neon branch actually
created, no Vercel project actually deployed. When that happens, it earns
its own chapter, written the same way every other chapter in this book was:
state what was tried, report what actually happened, including the parts
that didn't work on the first attempt — because if this project has proven
anything worth carrying forward past its own twelve weeks, it's that the
parts that didn't work on the first attempt are usually the parts worth
writing down most carefully.
