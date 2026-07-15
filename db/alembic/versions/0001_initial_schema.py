"""initial schema: tenants, policies, request_metrics hypertable, audit_events, RLS

Revision ID: 0001
Revises:
Create Date: 2026-07-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb"))

    # Non-owner application roles — REVISION #4: the table owner bypasses Row-Level
    # Security regardless of any policy defined below, so the app must never connect
    # as the owning/migration role. `shieldstream_app` is tenant-scoped and RLS-bound
    # (gateway: tenant/policy lookups, future tenant-facing queries). `shieldstream_worker`
    # is BYPASSRLS deliberately: the analytics/alert consumers write aggregated rows
    # spanning many tenants in a single batched upsert (Week 7), which is structurally
    # incompatible with a single session-scoped app.tenant_id. Passwords are the same
    # `localdev_only` convention already committed for the postgres superuser in
    # docker-compose.yml — local-dev only; Phase 6 sources real credentials from Neon's
    # per-role secrets, never version control.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'shieldstream_app') THEN
                    CREATE ROLE shieldstream_app LOGIN PASSWORD 'localdev_only';
                END IF;
                IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'shieldstream_worker') THEN
                    CREATE ROLE shieldstream_worker LOGIN PASSWORD 'localdev_only' BYPASSRLS;
                END IF;
            END
            $$;
            """
        )
    )

    # -- Tenants --------------------------------------------------------------
    op.execute(
        sa.text(
            """
            CREATE TABLE tenants (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL,
                api_key_hash TEXT NOT NULL UNIQUE,   -- SHA-256 hex digest, see REVISION #1
                upstream_base_url TEXT NOT NULL,     -- REVISION #2: proxy target, missing in original guide
                ip_hash_salt TEXT NOT NULL DEFAULT encode(gen_random_bytes(16), 'hex'),  -- REVISION #2: per-tenant salt for Week 6 IP hashing
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )

    # -- Policies (hot-reloadable, see Week 5/9) -------------------------------
    op.execute(
        sa.text(
            """
            CREATE TABLE policies (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                route_pattern TEXT NOT NULL,
                rate_limit_rps INT NOT NULL,
                rate_limit_window_s INT NOT NULL DEFAULT 60,
                owasp_rules_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                custom_rules JSONB NOT NULL DEFAULT '[]'::jsonb,
                policy_version INT NOT NULL DEFAULT 1,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    # REVISION #5: plain CREATE INDEX, not CONCURRENTLY — CONCURRENTLY cannot run
    # inside Alembic's managed transaction, and on this empty just-created table
    # there is no lock-contention cost to avoid (the guide's own reasoning for
    # CONCURRENTLY applies to indexing a live table with existing data/traffic).
    op.execute(sa.text("CREATE INDEX idx_policies_tenant ON policies(tenant_id)"))

    # -- Time-series request metrics: becomes a TimescaleDB hypertable ---------
    op.execute(
        sa.text(
            """
            CREATE TABLE request_metrics (
                bucket TIMESTAMPTZ NOT NULL,
                tenant_id UUID NOT NULL,
                endpoint TEXT NOT NULL,
                method TEXT NOT NULL,
                status_2xx BIGINT NOT NULL DEFAULT 0,
                status_4xx BIGINT NOT NULL DEFAULT 0,
                status_5xx BIGINT NOT NULL DEFAULT 0,
                blocked_count BIGINT NOT NULL DEFAULT 0,
                total_requests BIGINT NOT NULL DEFAULT 0,
                p50_latency_ms DOUBLE PRECISION,
                p99_latency_ms DOUBLE PRECISION,
                threat_score_avg DOUBLE PRECISION,
                PRIMARY KEY (bucket, tenant_id, endpoint, method)
            )
            """
        )
    )
    op.execute(sa.text("SELECT create_hypertable('request_metrics', 'bucket')"))

    # -- Row-Level Security: structural multi-tenant isolation -----------------
    # REVISION #4: ENABLE alone is not sufficient — the table owner (and any
    # BYPASSRLS role) ignores RLS policies by design. FORCE makes the policy
    # apply even to the table owner; `shieldstream_app` (used by the RLS
    # verification test and any future tenant-facing query path) is neither
    # the owner nor BYPASSRLS, so it is the role actually governed by this policy.
    op.execute(sa.text("ALTER TABLE request_metrics ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE request_metrics FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON request_metrics
                USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            """
        )
    )

    # -- Append-only audit log --------------------------------------------------
    op.execute(
        sa.text(
            """
            CREATE TABLE audit_events (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                event_type TEXT NOT NULL,
                tenant_id UUID,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )

    # -- Grants ------------------------------------------------------------------
    # shieldstream_app: tenant/policy lookups from the gateway's hot path, plus
    # RLS-governed access to request_metrics for any future tenant-facing query.
    op.execute(
        sa.text(
            """
            GRANT SELECT ON tenants, policies TO shieldstream_app;
            GRANT SELECT, INSERT, UPDATE ON request_metrics TO shieldstream_app;
            GRANT INSERT ON audit_events TO shieldstream_app;
            """
        )
    )
    # shieldstream_worker: analytics/alert consumers and the admin policy API —
    # BYPASSRLS is required (see role creation comment above), and these are the
    # only writers to request_metrics/audit_events/policies at runtime.
    op.execute(
        sa.text(
            """
            GRANT SELECT ON tenants TO shieldstream_worker;
            GRANT SELECT, UPDATE ON policies TO shieldstream_worker;
            GRANT SELECT, INSERT, UPDATE ON request_metrics TO shieldstream_worker;
            GRANT INSERT ON audit_events TO shieldstream_worker;
            """
        )
    )

    # -- Hourly rollup (used by the Week 9 dashboard) ----------------------------
    # REVISION #6: the guide models this as a TimescaleDB continuous aggregate
    # (`CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous)`), but
    # Timescale hard-rejects that on any RLS-enabled hypertable outright —
    # "cannot create continuous aggregate on hypertable with row security"
    # (discovered running this migration, not a documentation-only concern).
    # The continuous aggregate's incremental-refresh background worker runs
    # outside any request's session context, so it has no `app.tenant_id` to
    # evaluate the RLS policy against — Timescale refuses to create it rather
    # than silently materializing across tenants. Since REVISION #4's RLS on
    # request_metrics is the security-load-bearing decision here, it stays;
    # the rollup becomes a plain table, refreshed periodically by
    # `shieldstream_worker` (BYPASSRLS, already the sole writer to
    # request_metrics — Week 7) via an explicit
    # `INSERT ... ON CONFLICT DO UPDATE` upsert (built in Phase 5, alongside
    # the dashboard that actually reads it). Same RLS treatment as
    # request_metrics is applied here for defense in depth on the rollup too.
    op.execute(
        sa.text(
            """
            CREATE TABLE request_metrics_hourly (
                hour TIMESTAMPTZ NOT NULL,
                tenant_id UUID NOT NULL,
                endpoint TEXT NOT NULL,
                total_requests BIGINT NOT NULL DEFAULT 0,
                blocked_count BIGINT NOT NULL DEFAULT 0,
                threat_score_avg DOUBLE PRECISION,
                PRIMARY KEY (hour, tenant_id, endpoint)
            )
            """
        )
    )
    op.execute(sa.text("ALTER TABLE request_metrics_hourly ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE request_metrics_hourly FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            """
            CREATE POLICY tenant_isolation ON request_metrics_hourly
                USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
            """
        )
    )
    op.execute(
        sa.text(
            """
            GRANT SELECT ON request_metrics_hourly TO shieldstream_app;
            GRANT SELECT, INSERT, UPDATE ON request_metrics_hourly TO shieldstream_worker;
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON request_metrics_hourly"))
    op.execute(sa.text("DROP TABLE IF EXISTS request_metrics_hourly"))
    op.execute(sa.text("DROP TABLE IF EXISTS audit_events"))
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation ON request_metrics"))
    op.execute(sa.text("DROP TABLE IF EXISTS request_metrics"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_policies_tenant"))
    op.execute(sa.text("DROP TABLE IF EXISTS policies"))
    op.execute(sa.text("DROP TABLE IF EXISTS tenants"))
    op.execute(sa.text("DROP ROLE IF EXISTS shieldstream_worker"))
    op.execute(sa.text("DROP ROLE IF EXISTS shieldstream_app"))
