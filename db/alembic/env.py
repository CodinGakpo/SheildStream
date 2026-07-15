import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migrations run as the table-owning role (DDL, RLS setup, role creation),
# distinct from the app's restricted `shieldstream_app` role used at runtime
# (see revision #4 in DECISIONS.md — RLS is bypassable by the table owner).
migration_url = os.environ.get(
    "MIGRATION_DATABASE_URL",
    "postgresql://shieldstream:localdev_only@localhost:5432/shieldstream",
)
# Alembic uses the sync psycopg2 driver regardless of the app's asyncpg URL.
migration_url = migration_url.replace("+asyncpg", "")
config.set_main_option("sqlalchemy.url", migration_url)

target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
