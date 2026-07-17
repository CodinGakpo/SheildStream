# Chapter 16 — Three Bottlenecks the Code Review Never Would Have Found

Everything built through Chapter 15 had been verified correct — under
concurrency (Chapter 7), under failure (Chapters 8, 10), across a real
browser (Chapter 14), across process boundaries (Chapter 15). None of that
verification had ever pushed the system to genuinely high concurrency at
once. This week did: a Locust-driven load test ramping from zero to one
thousand concurrent users over sixty seconds, holding there for five minutes,
then ramping back down — with a weighted traffic mix (seventy percent plain
GET requests, twenty-five percent POST, five percent a low-frequency SQL
injection probe, deliberately infrequent enough to keep exercising Chapter
11's alert path under load without dominating the latency numbers the test
is actually trying to measure).

## What actually broke, in the order it was found

**The first run failed almost completely: a 63.7% error rate**, with average
latency around thirteen seconds. The cause was a connection pool — the
HTTP client the gateway uses to reach each tenant's upstream service had a
hard cap of one hundred concurrent connections, a number that had been
entirely adequate through every prior week's testing, since nothing before
this point had ever generated more than a few dozen concurrent requests.
At a thousand concurrent users, roughly nine hundred requests were queuing
for a pool slot that wasn't coming, and blowing through the two-second pool
acquisition timeout configured back in Chapter 6. The fix raised the pool's
capacity well past the test's own peak concurrency.

**The second run surfaced a different failure: database connection pool
exhaustion**, showing up in the logs as an explicit
`sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 5 reached`.
The root cause traces directly back to Chapter 6's authentication cache: a
fixed API key with a thirty-second cache TTL means that when many *new*
concurrent connections arrive faster than the cache can warm — precisely what
a load test's ramp-up phase does, and precisely what a real flash crowd would
do too — every one of them falls through to Postgres simultaneously. A real
thundering herd, not a hypothetical one. Fifteen total connections wasn't
enough headroom to absorb that burst. The pool was widened, with the increase
sized deliberately against Postgres's own confirmed maximum connection count,
so the fix didn't just move the bottleneck to a different pool.

**The third failure was the least obvious of the three: the container's own
file-descriptor limit.** The default soft limit of 1,024 open files, entirely
invisible in every prior week's testing, was exhausted by a thousand
concurrent client sockets plus the gateway's own upstream and
database/Redis connections all competing for descriptors at once. This
surfaced as `OSError(24, 'Too many open files')`, logged directly by
Python's `asyncio` runtime itself — not as a clean exception FastAPI's own
error handling ever saw, which is exactly why it never showed up as an
ordinary `500` the way the first two bottlenecks did. The fix raised the
container's file-descriptor ulimit well past what a thousand-user test could
plausibly exhaust.

A fourth issue was self-inflicted in the load-testing tooling rather than in
the gateway itself: the dedicated load-test tenant's rate limit was
initially configured as five thousand requests over a sixty-second window —
which, read correctly against the rate limiter's actual semantics from
Chapter 7 (a request *count* inside a window, not a literal per-second rate),
works out to roughly eighty-three requests per second sustained, not five
thousand. Real `429` rejections were showing up mixed into the failure data
the test was supposed to be using to isolate the *other* three bottlenecks,
muddying the signal. Fixed by setting the window to one second, making the
configuration's name and its actual behavior finally agree.

## The honest result, reported as measured

After all three fixes: **a 0.005% error rate** — two failures out of
47,311 total requests — comfortably inside the guide's target of under
0.1%, and the direct, measurable payoff of chasing down and fixing three real
bottlenecks instead of assuming the system would simply handle a thousand
users because it had handled everything smaller correctly.

Latency is a different story, and it's reported the same way every other
number in this project is reported: as measured, including the part that
missed the target. p50 came in at 4.2 seconds; p99 at 33 seconds — both far
past the guide's stated targets of fifteen and fifty *milliseconds*. This
was not glossed over or reframed as a partial success. The root cause was
confirmed directly via `docker stats`: the gateway's single CPU core sat at
roughly 100% utilization for the entire five-minute hold phase, and response
times climbed monotonically throughout it — median latency starting around
350 milliseconds at the beginning of the ramp and climbing past four seconds
by the end of the hold. That's the textbook signature of sustained overload:
once the arrival rate of new work exceeds one process's actual service rate,
a queue simply never drains, and grows for as long as the overload continues.

This is a capacity ceiling specific to *this* deployment shape — one
`uvicorn` worker, in one container, on this particular machine's hardware —
not evidence that the application itself is inefficient. Chapter 6 already
measured the gateway's genuine per-request overhead in isolation, at a
concurrency the downstream target could actually sustain, and found it to be
around six milliseconds. That number didn't change; what changed here is
that a single process, however efficient per request, still has a finite
total capacity, and a thousand concurrent users exceeded it. The real
mitigation isn't optimizing this single container further — it's horizontal
scaling, which Chapter 13 already proved works, live, across independently
running gateway replicas connected by the same Redis Pub/Sub bridge that
makes the dashboard's fan-out work. That result is treated as the actual
answer to this chapter's latency numbers, not chased further by tuning a
single-process configuration that was never going to reach millisecond
latency at this concurrency regardless of how it was tuned.

## Why this chapter matters more than its numbers

None of the three real bottlenecks found here were visible from reading the
code. Every one of them only exists as a property of concurrent load — a
connection pool sized correctly for every scenario tested through Week 10 and
wrong for the first scenario that genuinely stressed it. This is the same
lesson Chapter 7 made about the rate limiter's own race condition, generalized
to the whole system: correctness under load isn't something you can reason
your way to from first principles with full confidence. It's something you
find out by actually generating the load and reading, carefully, what
actually happened.
