from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=5)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


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
