import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.config import settings
from app.http_client import shutdown_http_client, startup_http_client
from app.policy_invalidation import listen_for_invalidations
from app.redis_client import get_pubsub_redis
from app.routes.admin import router as admin_router
from app.routes.dashboard_ws import fanout_loop
from app.routes.dashboard_ws import router as dashboard_ws_router
from app.routes.proxy import router as proxy_router
from app.tracing import setup_tracing, shutdown_tracing

# Strong references to the two background tasks below — same care taken with
# the Week 6 event emitter's fire-and-forget tasks (app/event_emitter.py):
# the event loop holds asyncio.create_task() results only weakly, so without
# this the garbage collector is free to silently cancel a not-yet-finished
# task.
_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing()
    await startup_http_client()

    pubsub_redis = await get_pubsub_redis()
    for coro in (listen_for_invalidations(pubsub_redis), fanout_loop(pubsub_redis)):
        task = asyncio.create_task(coro)
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    yield

    for task in list(_background_tasks):
        task.cancel()
    await shutdown_http_client()
    shutdown_tracing()


app = FastAPI(title="ShieldStream Gateway", version="0.1.0", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])
app.include_router(proxy_router)
app.include_router(admin_router)
app.include_router(dashboard_ws_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version}
