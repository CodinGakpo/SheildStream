import asyncio

import fakeredis.aioredis
import pytest
from prometheus_client import REGISTRY

from app.event_emitter import STREAM_KEY, _background_tasks, emit_event
from tests.test_events import make_event


async def drain_background_tasks():
    # emit_event is fire-and-forget; tests must explicitly wait for the
    # scheduled tasks to finish before asserting on their effects.
    while _background_tasks:
        await asyncio.gather(*list(_background_tasks), return_exceptions=True)


@pytest.fixture
def redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


async def test_emit_writes_one_stream_entry_with_string_fields(redis):
    emit_event(redis, make_event())
    await drain_background_tasks()

    assert await redis.xlen(STREAM_KEY) == 1
    entries = await redis.xrange(STREAM_KEY)
    _msg_id, fields = entries[0]
    assert fields["tenant_id"] == "t1"
    assert fields["status_code"] == "200"
    assert fields["rate_limited"] == "0"
    assert fields["remote_ip_hash"] == "abc123"


async def test_emit_returns_before_write_completes(redis):
    # The task is scheduled, not awaited: immediately after emit_event
    # returns, the write may not have happened yet — that's the point.
    emit_event(redis, make_event())
    assert len(_background_tasks) == 1
    await drain_background_tasks()
    assert len(_background_tasks) == 0


async def test_emit_failure_is_logged_and_counted_not_raised(caplog):
    class ExplodingRedis:
        async def xadd(self, *args, **kwargs):
            raise ConnectionError("simulated redis outage")

    def counter_value() -> float:
        return REGISTRY.get_sample_value("shieldstream_event_emit_failures_total") or 0.0

    before = counter_value()
    emit_event(ExplodingRedis(), make_event())
    await drain_background_tasks()

    # The failure surfaced as a metric + log line — never as an exception
    # propagating into the request that scheduled it.
    assert counter_value() == before + 1
    assert any("event_emit_failed" in r.message for r in caplog.records)
