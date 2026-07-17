import httpx

_client: httpx.AsyncClient | None = None


async def startup_http_client() -> None:
    global _client
    # Week 11 finding: max_connections=100 was fine under every prior week's
    # test traffic (never more than a few dozen concurrent requests), but at
    # the load test's 1000 concurrent users, requests queued for a pool slot
    # and blew the 2s pool timeout in routes/proxy.py — a self-inflicted
    # bottleneck, not the gateway's own request handling. Raised past the
    # load test's peak concurrency with headroom.
    _client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=1200, max_keepalive_connections=200),
    )


async def shutdown_http_client() -> None:
    if _client is not None:
        await _client.aclose()


async def get_http_client() -> httpx.AsyncClient:
    assert _client is not None, "http client not initialized — lifespan did not run"
    return _client
