from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from app.config import settings
from app.http_client import shutdown_http_client, startup_http_client
from app.routes.proxy import router as proxy_router
from app.tracing import setup_tracing, shutdown_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_tracing()
    await startup_http_client()
    yield
    await shutdown_http_client()
    shutdown_tracing()


app = FastAPI(title="ShieldStream Gateway", version="0.1.0", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])
app.include_router(proxy_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version}
