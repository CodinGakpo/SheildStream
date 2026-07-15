import httpx

_client: httpx.AsyncClient | None = None


async def startup_http_client() -> None:
    global _client
    _client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )


async def shutdown_http_client() -> None:
    if _client is not None:
        await _client.aclose()


async def get_http_client() -> httpx.AsyncClient:
    assert _client is not None, "http client not initialized — lifespan did not run"
    return _client
