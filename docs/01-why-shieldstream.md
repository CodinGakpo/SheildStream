# Chapter 1 — Why ShieldStream

## The problem, stated plainly

Most APIs that need protecting don't need Cloudflare's API Shield or AWS WAF —
they need something much smaller: a thing that sits in front of an HTTP service,
stops any one caller from overwhelming it, notices when traffic looks like an
attack instead of normal use, and tells a human about it in something close to
real time. That's the whole brief. It sounds simple. It isn't, and the reason it
isn't is the actual subject of this book.

ShieldStream is a distributed API security gateway: a reverse proxy that
enforces per-tenant rate limits, detects SQL injection and other OWASP-class
attacks and statistically anomalous traffic patterns, and streams what it sees
to a live dashboard — all without becoming the slowest part of the request path
it's supposed to be protecting. It was built end-to-end, from an empty repository
to a chaos-tested, load-tested, observability-instrumented system, over the
course of twelve implementation weeks, following a set of guides that were
deliberately treated as a *starting point*, not a specification to copy
verbatim.

## Why this, and not something simpler

Three canonical systems-design interview questions are "design a rate limiter,"
"design a real-time analytics pipeline," and "design an API gateway." ShieldStream
is, structurally, all three at once, because in a real system they aren't
separable — the rate limiter needs to know about tenants, the analytics pipeline
needs to know about the rate limiter's decisions (a blocked request is data too),
and the gateway is the one place both of them have to run without adding
meaningful latency to a request that's just passing through.

That constraint — *do all of this without becoming the bottleneck* — is what
makes the project interesting. It rules out the easy answers. A synchronous
write to Postgres on every request is easy and wrong (it puts a database's
latency on the critical path of every proxied call). A naive rate limiter is
easy and wrong (it either allows a double-limit burst at a window boundary, or
it has a race condition under concurrency). A dashboard that polls is easy and
wrong (it doesn't feel real-time, and it doesn't scale). Every one of those
"easy and wrong" answers shows up somewhere in this book, considered and
rejected, with the reasoning for rejecting it written down at the time, not
reconstructed afterward to sound more decisive than it was.

## What "done" means here

Four properties, in order of how much they shaped the actual engineering:

1. **Correct under concurrency.** A rate limiter that works when tested one
   request at a time and breaks under 100 concurrent requests isn't a rate
   limiter, it's a demo. Chapter 7 is entirely about proving — not asserting —
   that the limiter is race-free.
2. **Correct under failure.** Redis will go down. A consumer process will get
   killed mid-batch. A WebSocket connection will drop. The system needs a
   defined, tested behavior for each of those, not an assumption that they
   won't happen. Chapters 8, 10, and 18 are all, at their core, about this.
3. **Honest about its own limits.** Nowhere in this project does a chapter
   claim a benchmark number that wasn't actually measured, or paper over a
   finding that came out worse than hoped. Chapter 16's load test numbers are
   reported as measured, including the parts that missed the target and the
   reason they missed it. This matters more than it sounds like it should —
   it's the difference between a project that teaches something and a project
   that performs having taught something.
4. **Built the way it would actually be operated.** Observability (Chapter
   15), chaos testing (Chapter 18), and a real CI/CD pipeline (Chapter 19)
   aren't epilogue features bolted on at the end for completeness — the guides
   scheduled them for the last third of the project on purpose, after there
   was a real system worth observing and worth breaking on purpose.

## The guides, and the decision to not just follow them

ShieldStream was built by following three implementation guides, covering
Weeks 0 through 12, that lay out a week-by-week plan: what to build, in what
order, with reference pseudocode for the trickier pieces. The guides are good —
detailed, opinionated, and mostly correct. "Mostly" is the operative word, and
it's the reason this project has a `DECISIONS.md` at all.

The working rule, from the very first week, was: **follow the plan, but verify
every piece of it, and when it's wrong, fix it and write down why.** This turned
out to matter constantly. The guide's API-key hashing scheme was slow in a way
that doesn't actually buy any security (Chapter 3). Its Row-Level Security setup
had a gap that would have made the whole multi-tenancy guarantee silently
inert (Chapter 4). Its rate-limiter middleware would have crashed on its first
real request (Chapter 8). Its analytics consumer's crash-recovery logic looks
plausible on the page and loses data the first time you actually kill it
(Chapter 10). None of these are guide *typos* — they're the kind of bug that
only shows up when pseudocode meets a running process, and finding them required
treating "the guide says so" as a hypothesis to test, not an answer to trust.

That's the spine of the whole book: not "here's what we built," but "here's the
problem, here's what we tried, here's what actually happened when we ran it,
and here's what we changed because of that." Twenty chapters of it, covering
twelve weeks.
