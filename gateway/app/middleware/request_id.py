import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Tenant-independent (unlike rate limiting — see rate_limit.py's REVISION #3
# docstring), so a real Starlette middleware is safe here: nothing in this
# module ever touches request.state.tenant, which only exists after the
# get_tenant dependency runs.
class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            # Bound to a contextvar, not a per-request object — must be
            # cleared explicitly or it would leak into whatever request
            # happens to run next on the same task.
            structlog.contextvars.clear_contextvars()
        response.headers["X-Request-Id"] = request_id
        return response
