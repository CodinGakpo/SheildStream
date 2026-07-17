# Chapter 11 — Two Tiers of Threat Detection

The analytics consumer (Chapter 10) proved that one process could read the
event stream durably. This week's question was different: could a *second*,
completely independent process read the *same* stream, for a completely
different purpose, without either one knowing the other exists?

## Two readers, one stream, zero coupling

Redis Streams' consumer-group model makes this almost free once the stream
itself exists. A second consumer group — `alert-cg` — reads the identical
`request_events` stream that `analytics-cg` already drains, from its own
independent offset. One `XADD` from the gateway now feeds two structurally
separate pipelines, with no duplicated producer work and no data duplication
between them, and — critically — neither group's lag, backlog, or crash
history can touch the other's. This was proven directly, not just assumed
from the Streams model's documentation: with the analytics consumer stopped,
twenty-five new requests pushed `analytics-cg`'s lag to twenty-five while
`alert-cg` held steady at zero lag the entire time, and analytics caught back
up cleanly once restarted.

The alert consumer runs two independent detection tiers on every event it
reads, and publishes structured, severity-tagged alerts to a Redis Pub/Sub
channel — a channel with no subscriber yet, since the dashboard that will
actually consume it doesn't exist until Week 9 (Chapter 14). Rather than wait
for that consumer to exist before locking down the message shape, the JSON
contract was fixed and verified now, live, via a raw `redis-cli SUBSCRIBE` —
so the *shape* of what gets published is settled and tested before anything
downstream depends on it.

## Tier 1: signature matching, and the two ways it can hurt you

The first tier is regex-based OWASP signature detection — patterns for SQL
injection (`UNION...SELECT`, `OR 1=1`, trailing-comment injection,
`DROP TABLE`), cross-site scripting (`<script`, `onerror=`/`onload=`
handlers, `javascript:` URIs), and path traversal, including both
single- and double-URL-encoded variants (`../` and its encoded forms). This
is honestly, explicitly the same approach a basic WAF ruleset takes, and it
carries that approach's well-known limitation openly rather than overselling
it: it catches *known literal shapes*. An attacker who obfuscates past a
naive pattern — alternate encodings, splitting a keyword across a comment —
gets through it. That's precisely why it's paired with a second, structurally
different tier rather than relied on alone.

Two hardening decisions went beyond what the guide itself specified:

**ReDoS safety.** This engine runs its patterns against attacker-controlled
input, by design — that's the entire point of a threat detector. That also
means a regex pattern containing nested quantifiers would itself become a
denial-of-service vector *against the detector*, via catastrophic
backtracking on a crafted input. Every pattern here is kept simple, anchored,
and linear in its worst-case matching cost (bounded repetition like
`UNION.{1,40}?SELECT` rather than unbounded), and the whole rule set is
tested against a fifty-thousand-character adversarial input string that has
to complete matching in under half a second. A signature engine that can be
turned into a slow-loris attack against itself isn't a security feature.

**Scanning fields separately, not concatenated (a deliberate deviation from
the guide).** The guide's approach concatenates `query_string + " " +
user_agent` into one combined string before scanning it. That concatenation
creates a real gap: content trailing in one field can defeat a
`$`-anchored pattern meant for the *other* field — specifically, the SQL
trailing-comment rule, which anchors to the end of its intended target, can
be silently neutralized by whatever happens to follow it once the two fields
are glued together. Scanning the two fields independently preserves each
pattern's own anchoring; a rule fires if it matches *either* field on its
own terms, with no cross-field interference possible.

## Tier 2: a statistical baseline that needs no training and is honest about what it can't see

The second tier learns "normal" traffic per endpoint using an exponentially
weighted moving average (EWMA, α = 0.1) — operational from the very first
event, with no training phase and no model file to manage. A z-score
(deviation from the baseline, divided by the EWMA's own estimated standard
deviation) turns that into a threshold: `z > 3.0` triggers an alert. Severity
is deliberately set to MEDIUM here, not HIGH — the distinction matters and is
stated plainly rather than glossed over: a signature match (Tier 1) is
high-confidence evidence of a specific known attack shape, while a
statistical deviation only means "unusual," not "malicious." Real traffic is
often bursty, closer to Poisson-distributed than smoothly normal, so this
tier is a cheap, interpretable heuristic — genuinely useful, but not a
rigorous statistical test — and its severity level says so honestly rather
than dressing it up as more certain than it is. A cold-start guard suppresses
scoring entirely until at least twenty samples have accumulated for a given
endpoint, because a standard-deviation estimate built from one or two data
points is noise — noise that would make the very next ordinary value read as
an enormous, spurious spike. This was verified directly: no alert fires
during that warmup window, on any endpoint, no matter how the traffic looks
during it.

**A real, honestly-documented sensitivity limit, found by actually testing
against a live spike rather than a synthetic one:** against a flat baseline,
a single sharp one-second spike consistently pins the z-score at
approximately 3.33, *regardless of how large the spike actually is* — because
the outlier itself inflates the EWMA's variance estimate in the very same
update step that's supposed to be measuring the deviation against it. The
consequence is that detection reliably fires for a spike concentrated in one
second (3.33 clears the 3.0 threshold), but a spike that *ramps up* gradually
across several seconds lets the moving mean chase it upward and can stay
under threshold the entire time. This is a genuine, mathematically explicable
limitation of this specific estimator — a windowed or more robust variance
calculation, or a fast/slow dual-EWMA ratio, would sharpen this — and it's
documented as a known limitation at this project's current scope rather than
silently fixed or silently ignored. Verified live: a thirty-second flat
baseline around six requests per second, followed by a spike to 173 requests
per second, scored z = 3.33 and correctly published a `BEHAVIORAL_ANOMALY`
alert; the *very next* second, at 129 requests per second (still well above
baseline), scored z = 1.86 and correctly did *not* re-fire — the moving
average had already started chasing the new level.

## Deduplication, and hardening it against the exact traffic it's meant to survive

Alerts sharing the same type and source within a sixty-second window collapse
into a single published alert carrying a running count — the same pattern
Alertmanager and PagerDuty use for the identical reason: alert fatigue is
worst precisely during a real sustained attack, when hundreds of scanner
probes a second would otherwise produce hundreds of near-identical alerts a
second, drowning out the signal exactly when it matters most. Verified with a
burst of fifty identical SQL-injection probes: exactly one alert published,
forty-nine correctly suppressed.

One hardening decision went beyond the guide's own dedup design: the
deduplication key includes a hashed source identifier that is,
structurally, attacker-controlled — an attacker who simply rotates their
apparent source on every request could grow the deduplication dictionary
without bound, turning the anti-noise mechanism itself into a
memory-exhaustion vector. Expired entries are now swept on access, time-gated
to roughly every five seconds so a genuine burst doesn't pay a full sweep's
cost on every single event. The statistical tier's own per-endpoint baseline
dictionary is bounded the same way, evicting any endpoint that's gone idle
for more than an hour.

## Delivery semantics, deliberately different from the analytics consumer

This consumer's acknowledgment behavior is the mirror image of Chapter 10's,
and for a specific reason tied to what each pipeline actually needs. The
analytics consumer does durable counting — a duplicate is harmless (the
upsert absorbs it), a loss is not, so it strictly writes-then-acknowledges
and replays its own pending entries on restart. The alert consumer does
*timely detection* — an alert is fundamentally a real-time signal, and
re-emitting a minutes-old alert from a replayed pending batch after a restart
would just be misdated noise, not useful information. So on restart, this
consumer *drops* its own pending entries (acknowledging them without
re-scanning), and in steady state it acknowledges *every* message it scans,
whether a rule matched or not — a deliberate correction beyond the guide's
own design, which only ever acknowledged matches and would have let the
pending-entries list grow without bound on entirely normal, non-matching
traffic. The accepted trade: missing a handful of alerts across a crash
window is preferable to double-alerting on stale detections, and a genuinely
sustained attack will simply re-trigger on the next live event regardless.

## Single replica, and why that's correct here (not just simpler)

The statistical tier's per-endpoint baseline is only meaningful if one
consumer sees the *entire* stream — sharding the stream across multiple
alert-consumer replicas would make every replica's view of per-endpoint RPS
an undercount, silently breaking the z-score math in a way that wouldn't
throw an error, just quietly produce wrong numbers. So this consumer runs as
exactly one replica, by design, unlike the analytics consumer, whose
idempotent counting logic makes it entirely safe to run in parallel.

## What the guide's own pseudocode couldn't run as written

The guide's reference `worker.py` for this consumer isn't runnable code —
it imports from packages that don't exist at the paths it names, references
several functions (`get_redis()`, `ensure_consumer_group()`,
`parse_event()`, a `RollingRpsCounter` class) that are never defined
anywhere in the guide, and its final acknowledgment step builds its list of
message IDs with a broken nested comprehension that wouldn't even parse. It
was rebuilt from the ground up using the *analytics* consumer's own
already-proven worker loop as the template — real consumer-group setup, the
stable-name pattern from Chapter 10, poison-tolerant event parsing — rather
than attempting to debug pseudocode that was never functional to begin with.

## What was verified

A SQL-injection probe and a double-URL-encoded path-traversal attempt each
produced a correctly structured alert within milliseconds of the request. A
burst of fifty identical probes collapsed to exactly one published alert. A
sustained spike against an already-warmed baseline correctly produced a
behavioral anomaly alert, with no alert during the cold-start window before
that. `alert-cg`'s progress was confirmed fully independent of
`analytics-cg`'s — stopping one leaves the other draining normally. The
alert JSON's shape was confirmed over a live subscription before any
consumer of it existed. And twenty-seven new unit tests — covering the
signature rules including a deliberately benign "select" string that must
*not* false-positive, the ReDoS timing bound, cold-start and spike and
eviction behavior, deduplication under burst and expiry, and RPS bucketing —
brought the consumers' total test count to thirty-five, all passing.
