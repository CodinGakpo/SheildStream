FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# --loop asyncio: uvicorn defaults to uvloop when available (uvicorn[standard]
# installs it). Found, via live fail-open testing (Week 5), a reproducible
# uvloop/anyio interaction bug: after a Redis connection attempt to an
# unreachable host times out, a *subsequent, completely unrelated* async DNS
# resolution (httpx resolving the downstream proxy target) also fails,
# consistently, for the full configured connect timeout — reproducible in
# isolation only under uvloop, never under the standard asyncio loop. See
# DECISIONS.md, Week 5. Correctness during exactly the failure scenario this
# week is about (a Redis outage) outweighs uvloop's throughput edge for this
# project.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio"]
