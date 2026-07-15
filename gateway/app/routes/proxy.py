import httpx
from fastapi import APIRouter, Depends, Request, Response

from app.auth import get_tenant
from app.http_client import get_http_client
from app.middleware.rate_limit import enforce_rate_limit
from app.tracing import tracer

router = APIRouter()

_HOP_BY_HOP = {"host", "content-length", "connection"}
# x-api-key authenticates the caller to ShieldStream; the downstream service
# has no use for it, and forwarding it needlessly widens the credential's
# exposure surface to every proxied backend.
_STRIP_INBOUND = _HOP_BY_HOP | {"x-api-key"}


@router.api_route("/proxy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def proxy(
    path: str,
    request: Request,
    tenant: dict = Depends(get_tenant),
    rate_limit: dict = Depends(enforce_rate_limit),
    client: httpx.AsyncClient = Depends(get_http_client),
) -> Response:
    downstream_url = f"{tenant['upstream_base_url']}/{path}"
    body = await request.body()

    with tracer.start_as_current_span("proxy.forward") as span:
        span.set_attribute("http.downstream_url", downstream_url)
        span.set_attribute("shieldstream.tenant_id", tenant["id"])

        # Host is stripped so the downstream service sees its own hostname, not
        # ShieldStream's — forwarding it unmodified breaks any downstream logic
        # depending on Host (virtual hosting, generated absolute URLs).
        upstream_response = await client.request(
            method=request.method,
            url=downstream_url,
            headers={k: v for k, v in request.headers.items() if k.lower() not in _STRIP_INBOUND},
            content=body,
            params=request.query_params,
            timeout=httpx.Timeout(connect=2.0, read=10.0, write=5.0, pool=2.0),
        )
        span.set_attribute("http.status_code", upstream_response.status_code)

    response = Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers={
            k: v for k, v in upstream_response.headers.items() if k.lower() not in _HOP_BY_HOP
        },
    )
    # Set on every successful response, not just 429s — lets a well-behaved
    # client self-throttle proactively instead of discovering the limit by
    # being rejected (RFC 6585 territory, but these three headers aren't
    # formally standardized, just conventional).
    if rate_limit["limit"] is not None:
        response.headers["X-RateLimit-Limit"] = str(rate_limit["limit"])
        response.headers["X-RateLimit-Remaining"] = str(rate_limit["remaining"])
    return response
