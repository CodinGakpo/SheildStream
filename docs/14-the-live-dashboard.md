# Chapter 14 — A Live Dashboard, and the Bugs That Only Showed Up in a Real Browser

Chapter 11 built a threat-alert channel with no subscriber. Chapter 13's Part
A gave operators a way to change policy instantly. This chapter closes the
loop: an actual operator-facing dashboard, and the gateway-side infrastructure
that gets live data to it.

## Fan-out, and reusing a guarantee instead of building a new one

The gateway exposes a `/ws/dashboard` WebSocket endpoint. Any number of
dashboard clients can connect to any gateway replica, and all of them need to
see the same data — a threat alert or a metrics snapshot published on one
replica has to reach every connected client, regardless of which specific
replica each client happened to land on. The mechanism is the same Redis
Pub/Sub bridge built in Chapter 13: the gateway subscribes to two channels
(`dashboard:alerts`, already carrying real data from Chapter 11's alert
consumer with no subscriber until now; and a new `dashboard:metrics` channel,
publishing a per-second requests-per-second snapshot), and broadcasts
whatever arrives to every locally connected WebSocket client.

The per-second metrics snapshot needed a publisher, and rather than build new
coordination machinery to guarantee exactly one publisher exists across
however many gateway replicas are running, it's published by the alert
consumer itself — reusing the single-replica guarantee Chapter 11 *already*
established for a completely different reason (the statistical anomaly
tier's baseline is only correct if one process sees the whole stream).
That guarantee happens to solve this chapter's coordination problem too, for
free, without adding a second piece of leader-election infrastructure to own
and maintain.

On the broadcast side, each connected client's send is wrapped in its own
try/except — one dead or slow client failing to receive a message can never
block or crash delivery to every other client sharing the same broadcast
loop. A dashboard with fifty tabs open shouldn't have its worst-behaved tab
degrade the other forty-nine.

## The dashboard itself

Built with Next.js (App Router, TypeScript, Tailwind), Recharts for the RPS
chart, and a native browser `WebSocket` — no client library on top of it,
matching the guide's own specification for this piece. One page: a live
requests-per-second line chart fed by the metrics snapshots (capped at sixty
points, so a tab left open for hours doesn't grow its chart data without
bound), and a threat feed populated from the alert channel (capped at fifty
entries, with HIGH- and MEDIUM-severity alerts visually distinguished so an
operator's eye is drawn to the more urgent tier first).

The WebSocket connection's entire lifecycle lives inside one `useEffect`
hook, with two details that matter more than they look: an exponential
backoff reconnect strategy (starting at one second, capping at thirty, and
resetting back to one second the moment a connection actually succeeds — so
a genuinely dead gateway doesn't get hammered with reconnect attempts, but a
momentary blip recovers fast), and a cleanup function that explicitly closes
the socket and clears any pending reconnect timer. Without that cleanup, a
component unmount (or the WebSocket URL changing) could leave an orphaned
socket alive, still scheduling reconnect attempts into a component that no
longer exists — a classic React memory-leak shape that's easy to miss because
it doesn't crash anything, it just quietly accumulates.

## Chrome's own security model, discovered the hard way

The very first time the dashboard's WebSocket specs were run, they failed
with `net::ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS` — a Chrome security
feature (Local Network Access checks) that, by default in recent Chromium
versions, blocks a web page from opening a connection to a "more local"
network address than its own origin. Any page connecting to `localhost` falls
into this category. The browser's own generic `WebSocket.onerror` event gives
essentially no detail by design (this is deliberate on the web platform's
part, for security reasons unrelated to this specific check), which made the
actual cause invisible from application-level error handling alone. Diagnosing
it required Playwright's lower-level `page.on('websocket', ...)`
instrumentation — hooking the browser's own internal WebSocket lifecycle
events directly, bypassing the deliberately vague error the page itself
receives.

The fix for the test harness was straightforward: disable the check via
Chromium launch arguments, scoped entirely to the Playwright browser instance
used for testing. What makes this worth a chapter section rather than a
one-line footnote is that it isn't only a test-harness quirk. The real
dashboard — its own origin, eventually served from somewhere like Vercel —
connecting to the gateway's `ws://` (or `wss://`) endpoint is *exactly* the
same cross-origin, local-network connection pattern that Chrome blocks by
default. It's flagged here specifically so it doesn't get rediscovered as a
surprise in production later — Chapter 17's deployment plan already accounts
for it (the dashboard connects to the gateway over `wss://`, a real domain,
never `localhost`, which sidesteps the check by construction rather than by
disabling a browser security feature in a real user's browser, which isn't an
option).

## What was verified live

Every one of Week 9's "done-when" checks was tested against the real running
system, not asserted from the design. Hot-reload latency and optimistic
locking were covered in Chapter 13. This chapter adds: an injected
SQL-injection request produces a HIGH-severity entry in the dashboard's
threat feed within roughly two seconds of the request landing; restarting
the gateway container while a dashboard is connected flips its status
indicator to "reconnecting" and back to "live" entirely on its own, with no
page reload, driven by the backoff-reconnect logic described above; and the
multi-replica fan-out proof from Chapter 13 — one alert, triggered through
one replica, delivered identically to a client connected to a *different*
replica — is really a dashboard-layer verification as much as a Pub/Sub one,
since it's the dashboard's own WebSocket client receiving the proof.

Nine Playwright specs pass end-to-end against the full running stack: the
six from Chapter 12, plus three real-browser specs driving the actual
dashboard UI, replacing an earlier version of the same test file that had
exercised the raw WebSocket wire protocol directly, before there was a real
UI to drive instead.
