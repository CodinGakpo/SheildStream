import asyncio
import logging

from prometheus_client import Counter
from redis.asyncio import Redis

from app.events import RequestEvent

logger = logging.getLogger("shieldstream.events")

STREAM_KEY = "request_events"
STREAM_MAXLEN = 100_000

# Strong references to in-flight emit tasks. asyncio.create_task() returns a
# task the event loop holds only weakly — without a reference kept here, the
# garbage collector is permitted to collect a not-yet-finished task, silently
# cancelling the XADD. Works fine in most local testing (the task usually
# completes before any GC pass), then drops events intermittently under
# different scheduling conditions — the worst kind of bug to find later.
_background_tasks: set[asyncio.Task] = set()

EVENT_EMIT_FAILURES = Counter(
    "shieldstream_event_emit_failures_total",
    "Request events that failed to be written to Redis Streams",
)


def emit_event(redis: Redis, event: RequestEvent) -> None:
    """Fire-and-forget: schedules the XADD and returns immediately, without
    awaiting it. This is the entire mechanism by which event emission stays
    off the request's critical path — the response is already being sent to
    the client while the XADD happens on the event loop. Deliberately returns
    None so call sites can't accidentally reintroduce `await emit_event(...)`.
    """
    task = asyncio.create_task(_emit(redis, event))
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)


async def _emit(redis: Redis, event: RequestEvent) -> None:
    # MAXLEN ~ (approximate=True): exact trimming would force Redis to check
    # and trim on every single XADD; approximate trims only at efficient
    # macro-node boundaries of the stream's radix tree, letting the stream
    # briefly exceed the bound by a small margin in exchange for meaningfully
    # cheaper writes. Entries are drained by consumers within seconds, so the
    # approximate bound is functionally exact in practice.
    await redis.xadd(
        STREAM_KEY,
        event.to_redis_fields(),
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )


def _on_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    # An exception inside a fire-and-forget task propagates nowhere by
    # default — without this callback, a Redis hiccup during emission would
    # be an invisibly dropped event rather than a visible log line + metric.
    if not task.cancelled() and task.exception() is not None:
        logger.error("event_emit_failed", exc_info=task.exception())
        EVENT_EMIT_FAILURES.inc()
