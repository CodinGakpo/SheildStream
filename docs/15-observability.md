# Chapter 15 — Making the Invisible Visible

For nine weeks, ShieldStream had been correct — proven correct, repeatedly,
by the kind of live testing narrated in every chapter so far. It had not,
until this week, been *observable*. There was no way to ask the running
system "how are you doing right now" without either reading raw logs by hand
or writing a one-off script. Three tools this project had actually depended
on since Week 0 — `prometheus-client`, `structlog`, and OpenTelemetry's
propagation machinery — had been sitting in `requirements.txt` unused the
entire time, waiting for this week.

## Metrics, and the discipline that keeps them useful

Prometheus metrics are only as useful as their label design is disciplined,
and the discipline is worth stating explicitly because getting it wrong
doesn't fail loudly — it fails as a slow, silent memory leak in the metrics
backend itself. Every label used anywhere in this project's metrics is drawn
from a small, genuinely bounded set of values. `status_class` — `"2xx"`,
`"4xx"`, `"5xx"`, computed from the response code rather than using the raw
code as a label directly — is the only label present on the request counter.
Two things are deliberately *not* labels, and the reason is the same for
both: `endpoint` (the proxy route is user-controlled and structurally
unbounded — a path segment supplied by an attacker becomes a permanent new
time series if it's ever used as a label value) and `tenant_id` (the same
problem, arguably worse — every new tenant that ever signs up becomes another
permanent series that Prometheus has no mechanism to ever expire). This is
the practical failure mode label-cardinality guidance exists to prevent, made
concrete: an unbounded label doesn't error, doesn't warn, it just quietly
grows the metrics backend's memory footprint forever.

Two histograms exist, deliberately using different bucket scales, because
they're measuring genuinely different things at genuinely different
magnitudes. End-to-end proxy latency uses buckets from one millisecond to
one second — appropriate for a full request/response cycle. The rate
limiter's own Lua script call (Chapter 7) uses buckets from a tenth of a
millisecond to fifty milliseconds — because Prometheus's own *default*
histogram buckets bottom out around five milliseconds, which would put the
Lua script's entire distribution inside a single bucket and make the
histogram useless for the sub-two-millisecond precision that script was
specifically designed to hit.

## Tracing a request across a process boundary it doesn't know exists

The hardest observability problem this week wasn't inside the gateway at
all — it was the gap between the gateway and the analytics consumer
(Chapter 10), two entirely separate operating-system processes connected only
by a Redis Stream, with no shared memory and no direct call between them.
Before this week, a trace of a proxied request and the eventual database
write that request causes were two disconnected stories in Jaeger, sharing no
visible relationship.

The fix uses the W3C trace-context standard: at the moment a request is
proxied, the gateway injects the current span's `traceparent` — a compact,
standard string encoding the active trace's identity — directly into the
event written to the Redis Stream. When the analytics consumer later reads
that event, potentially seconds afterward and in a completely different
process, it extracts that same `traceparent` and opens its own span
*parented to it*, rather than starting a fresh, disconnected trace. The
result: one continuous Jaeger trace that spans both processes — the
gateway's authentication and rate-limiter and proxy-forward spans, followed
by the analytics consumer's own span for the exact same request, even though
the two pieces of work happened in different processes, on different
machines in a real deployment, seconds apart. This was confirmed by querying
Jaeger's own API directly, not just eyeballing its UI: fetching a specific
trace ID and confirming it genuinely contains spans from both service names.

## Logs that answer "show me everything that happened for this one request"

Structured logging closes a different gap. Every request gets one generated
identifier, bound once via a small piece of real Starlette middleware (not a
FastAPI dependency — this one's genuinely middleware-appropriate, unlike
Chapter 8's rate limiter, because it never needs to read anything that only
exists after a dependency resolves), and that identifier is merged
automatically into *every* log line emitted anywhere during that request's
handling — including log lines from code that was written years — well,
weeks — before this feature existed and has no idea a request ID exists at
all. That's the actual point of `contextvars`-based binding: correlation
without threading an ID explicitly through every function signature in the
codebase.

## A mistake in the alerting rule, found the same way every bug in this project gets found

The Grafana alert rule for this week — p99 proxy latency exceeding fifty
milliseconds, sustained for two minutes — failed on its very first live
evaluation, with a specific, informative error:
`invalid format of evaluation results ... only reduced data can be alerted
on`. The rule's threshold expression had been fed the raw output of a
`histogram_quantile()` query directly — technically a single-sample time
series, but still structurally *time-series data*, not a plain reduced
number, and Grafana's threshold node refuses to evaluate against anything
that isn't already reduced to a scalar. The fix inserted an explicit reduce
step between the query and the threshold check. This is a small bug, and it's
worth naming precisely because of what it *isn't*: it isn't a guide bug, and
it isn't a code-review-catchable mistake — it's a configuration bug in
hand-written provisioning, caught the exact same way every other bug in this
project got caught, by actually running it and reading what it actually said
back, rather than trusting that the YAML looked plausible.

## What was verified live

The `/metrics` endpoint was confirmed both correctly typed (Counter and
Histogram output matching Prometheus's own text-exposition format) and
genuinely load-responsive — checked before and after generating real traffic,
watching specific counters move from zero to accurate nonzero values, not
just confirming the endpoint returns *something*. The cross-process trace was
confirmed exactly as described above, via Jaeger's query API. Log
correlation was confirmed by grepping live container logs for one specific
request's ID and finding every log line from every layer of that request —
including third-party library debug output — sharing that same identifier.
The Grafana dashboard's panels were confirmed against real data through
Grafana's own datasource proxy, not just visually inspected in a browser.
And the alert rule's full lifecycle was proven against real wall-clock
timestamps, not inferred from log messages: it entered a pending state under
sustained induced latency, transitioned to firing at precisely the
configured two-minute mark, and returned to normal once the induced load
stopped and latency recovered — three states, three real timestamps, all
confirmed through the alerting API directly.
