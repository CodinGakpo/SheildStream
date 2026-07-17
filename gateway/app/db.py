from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

# pool_size/max_overflow raised from 10/5 — Week 11's load test hit
# `QueuePool limit ... overflow 5 reached` under 1000 concurrent users: a
# fixed API key's 30s auth-cache TTL (app/auth.py) means a burst of new
# connections arriving faster than the cache warms (exactly what a load
# test's ramp-up does) all fall through to the DB at once, a real thundering
# herd on cache-miss. Postgres's own max_connections is 100 (confirmed via
# `SHOW max_connections`); 50 here plus the admin engine's 4 and the
# analytics consumer's 5 leaves comfortable headroom.
engine = create_async_engine(settings.database_url, pool_size=30, max_overflow=20)
session_factory = async_sessionmaker(engine, expire_on_commit=False)

# Small dedicated pool for the low-volume admin API, connected as
# shieldstream_worker (BYPASSRLS) rather than the tenant-facing
# shieldstream_app — see app/config.py's admin_database_url comment.
admin_engine = create_async_engine(settings.admin_database_url, pool_size=2, max_overflow=2)
admin_session_factory = async_sessionmaker(admin_engine, expire_on_commit=False)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for routes that unconditionally need a DB session.

    Deliberately NOT used by auth.get_tenant: FastAPI resolves every
    Depends() parameter before the function body runs, so declaring this
    there would open a pooled connection on every request — including cache
    hits, which never touch the database. Under concurrency that pool
    acquisition serialized requests and blew up p50 proxy latency ~18x
    (2.6ms at c=1 vs 47ms at c=20) — see DECISIONS.md, Phase 2. Call
    `session_factory()` directly instead, only inside the cache-miss branch.
    """
    async with session_factory() as session:
        yield session


async def get_admin_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with admin_session_factory() as session:
        yield session
