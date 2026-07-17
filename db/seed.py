"""Seed two test tenants with a default policy each.

REVISION #1: the guide's Week 2 seed script (and Week 3 auth lookup) uses
bcrypt for the API key hash. Bcrypt is designed to slow down brute-forcing a
*low-entropy* secret (a human password); it is the wrong tool for a
high-entropy, randomly generated API key, where a bcrypt check costs ~100ms
per candidate and forces the guide's Week 3 auth path into an O(n) bcrypt
scan across every tenant on a cache miss. A random 128-bit+ API key already
has enough entropy that a fast, deterministic hash is sufficient — SHA-256
lets the lookup be a single indexed `WHERE api_key_hash = $1` query instead.
"""

import asyncio
import hashlib
import os
import secrets

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

MIGRATION_DATABASE_URL = os.environ.get(
    "MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://shieldstream:localdev_only@localhost:5433/shieldstream",
)

SEED_TENANTS = [
    {"name": "acme-corp", "upstream_base_url": "http://httpbin:80"},
    {"name": "globex-inc", "upstream_base_url": "http://httpbin:80"},
]


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def seed() -> None:
    engine = create_async_engine(MIGRATION_DATABASE_URL)
    async with engine.begin() as conn:
        for tenant in SEED_TENANTS:
            raw_key = f"sk_test_{secrets.token_hex(16)}"
            key_hash = hash_key(raw_key)

            result = await conn.execute(
                text(
                    """
                    INSERT INTO tenants (name, api_key_hash, upstream_base_url)
                    VALUES (:name, :hash, :upstream)
                    RETURNING id
                    """
                ),
                {"name": tenant["name"], "hash": key_hash, "upstream": tenant["upstream_base_url"]},
            )
            tenant_id = result.scalar_one()
            print(f"{tenant['name']}: id={tenant_id} api_key={raw_key}")  # shown ONCE, here only

            await conn.execute(
                text(
                    """
                    INSERT INTO policies (tenant_id, route_pattern, rate_limit_rps, rate_limit_window_s)
                    VALUES (:tenant_id, '/proxy/*', 100, 60)
                    """
                ),
                {"tenant_id": tenant_id},
            )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
