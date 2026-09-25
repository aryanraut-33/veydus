# ─────────────────────────────────────────────────────────────────
# VEYDUS — Alembic Environment Configuration
# ─────────────────────────────────────────────────────────────────
# What:  Configures how Alembic connects to the database and runs
#        migrations.  Supports both online (direct connection) and
#        offline (SQL script generation) modes.
# How:   Reads the database URL from the VEYDUS_MIGRATE_DB_URL env
#        var, falling back to the alembic.ini default.  Uses
#        synchronous psycopg2 connections (Alembic does not natively
#        support async migrations).
# ─────────────────────────────────────────────────────────────────
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object — provides access to alembic.ini values
config = context.config

# Set up Python logging from the alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Allow overriding the database URL via environment variable.
# This is how tests and CI inject a different database.
db_url = os.environ.get("VEYDUS_MIGRATE_DB_URL")
if db_url:
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    config.set_main_option("sqlalchemy.url", db_url)

# No SQLAlchemy MetaData for autogenerate — we write migrations by hand
# to keep full control over the security-critical schema (HLD §5.2).
target_metadata = None


def run_migrations_offline() -> None:
    """Generate SQL scripts without a live database connection.

    Useful for reviewing migration SQL before applying it.
    Usage: alembic upgrade head --sql
    """
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
    """Run migrations against a live database.

    Creates a synchronous connection (psycopg2) and runs each
    migration inside a transaction.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,  # No pooling for migrations
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
