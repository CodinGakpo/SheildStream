"""Admin API: hot-reload policy updates — Week 9 Part A.

Week 5's policy cache (app/policy.py) relies on a 10s TTL to notice a
change — fine for routine updates, too slow for an operator tightening a
rate limit on an actively-attacking tenant mid-incident. A successful update
here commits to Postgres — the source of truth — and only then publishes to
`policy:invalidate` (app/policy_invalidation.py), mirroring the "write first,
then signal" ordering already used for XACK in Week 7: publishing before the
commit lands risks every replica invalidating its cache for a change that
never actually took effect if the commit subsequently fails.
"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_tenant
from app.db import get_admin_db_session
from app.redis_client import get_redis

logger = logging.getLogger("shieldstream.admin")

router = APIRouter(prefix="/admin")


class PolicyUpdate(BaseModel):
    rate_limit_rps: int
    expected_version: int


@router.patch("/policies/{policy_id}")
async def update_policy(
    policy_id: str,
    update: PolicyUpdate,
    tenant: dict = Depends(get_tenant),
    db: AsyncSession = Depends(get_admin_db_session),
    redis: Redis = Depends(get_redis),
) -> dict:
    """Optimistic-locked update, scoped to the calling tenant's own policies.

    No separate admin-role concept exists yet, so the same X-API-Key that
    authenticates proxy traffic authenticates this action. A policy_id
    belonging to a different tenant returns 404, not 403 — the existence
    check below is scoped by tenant_id too, so this endpoint never confirms
    another tenant's policy IDs even exist.
    """
    result = await db.execute(
        text(
            """
            UPDATE policies
            SET rate_limit_rps = :rps, policy_version = policy_version + 1, updated_at = now()
            WHERE id = :id AND tenant_id = :tenant_id AND policy_version = :expected_version
            RETURNING tenant_id, route_pattern, policy_version
            """
        ),
        {
            "rps": update.rate_limit_rps,
            "id": policy_id,
            "tenant_id": tenant["id"],
            "expected_version": update.expected_version,
        },
    )
    row = result.mappings().one_or_none()

    if row is None:
        exists = await db.execute(
            text("SELECT 1 FROM policies WHERE id = :id AND tenant_id = :tenant_id"),
            {"id": policy_id, "tenant_id": tenant["id"]},
        )
        if exists.one_or_none() is None:
            raise HTTPException(404, "policy not found")
        raise HTTPException(409, "policy was modified concurrently, refetch and retry")

    await db.commit()  # source of truth is durable BEFORE we tell anyone about it

    await redis.publish(
        "policy:invalidate",
        json.dumps(
            {
                "tenant_id": str(row["tenant_id"]),
                "route_pattern": row["route_pattern"],
                "new_version": row["policy_version"],
            }
        ),
    )

    return {
        "policy_id": policy_id,
        "rate_limit_rps": update.rate_limit_rps,
        "policy_version": row["policy_version"],
    }
