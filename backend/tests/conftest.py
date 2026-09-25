# ─────────────────────────────────────────────────────────────────
# VEYDUS — Test Configuration (root conftest)
# ─────────────────────────────────────────────────────────────────
# What:  Session-scoped fixtures that start a pgvector container,
#        create roles, run the Alembic migration, and provide
#        connection factories for tests.
# How:   Uses testcontainers to spin up an ephemeral PostgreSQL 16
#        instance with pgvector.  Roles are created via direct SQL,
#        then Alembic runs the migration as veydus_migrate.
# Why:   Every test suite run gets a clean, isolated database.
#        No shared state between test runs, no dependency on a
#        running Docker Compose stack.
# Deps:  testcontainers[postgres], psycopg2-binary, alembic
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import os
from pathlib import Path

import psycopg2
import pytest

try:
    from testcontainers.community.postgres import PostgresContainer
except ImportError:
    from testcontainers.postgres import PostgresContainer

# ── Constants ────────────────────────────────────────────────────
_PG_IMAGE = "pgvector/pgvector:pg16"
_PG_DB = "veydus"
_PG_USER = "postgres"
_PG_PASSWORD = "postgres"

# Role credentials (must match backend/docker/init-db.sql)
MIGRATE_USER = "veydus_migrate"
MIGRATE_PASSWORD = "veydus_migrate_pw"
APP_USER = "veydus_app"
APP_PASSWORD = "veydus_app_pw"

# SQL to create the two application roles (same as init-db.sql,
# but executed via psycopg2 instead of psql).
_INIT_ROLES_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE ROLE veydus_migrate WITH LOGIN PASSWORD 'veydus_migrate_pw' BYPASSRLS;
CREATE ROLE veydus_app WITH LOGIN PASSWORD 'veydus_app_pw';
GRANT ALL PRIVILEGES ON DATABASE veydus TO veydus_migrate;
GRANT CONNECT ON DATABASE veydus TO veydus_app;
GRANT ALL PRIVILEGES ON SCHEMA public TO veydus_migrate;
GRANT USAGE ON SCHEMA public TO veydus_app;
"""


@pytest.fixture(scope="session")
def pg_container():
    """Start an ephemeral pgvector PostgreSQL 16 container.

    Session-scoped: one container per test run.  Automatically
    stopped and removed when the test session ends.
    """
    with PostgresContainer(
        image=_PG_IMAGE,
        dbname=_PG_DB,
        username=_PG_USER,
        password=_PG_PASSWORD,
    ) as container:
        yield container


@pytest.fixture(scope="session")
def db_config(request):
    """Extract connection parameters from environment or the running container.

    Returns a dict usable with psycopg2.connect(**db_config, user=...).
    If TEST_DB_HOST is set (e.g., CI service container or local docker-compose),
    it connects directly. Otherwise, it spins up an ephemeral testcontainer.
    """
    host = os.environ.get("TEST_DB_HOST")
    if host:
        return {
            "host": host,
            "port": int(os.environ.get("TEST_DB_PORT", "5432")),
            "dbname": os.environ.get("TEST_DB_NAME", _PG_DB),
        }

    container = request.getfixturevalue("pg_container")
    return {
        "host": container.get_container_host_ip(),
        "port": container.get_exposed_port(5432),
        "dbname": _PG_DB,
    }


@pytest.fixture(scope="session")
def migrated_db(db_config):
    """Create roles and run the Alembic migration.

    Session-scoped: runs once, all tests share the migrated schema.
    The migration runs as veydus_migrate (the table owner).

    Steps:
      1. Connect as postgres superuser
      2. Execute role-creation SQL
      3. Run Alembic upgrade head as veydus_migrate
    """
    # 1. Create roles as superuser
    conn = psycopg2.connect(**db_config, user=_PG_USER, password=_PG_PASSWORD)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(_INIT_ROLES_SQL)
    conn.close()

    # 2. Run Alembic migration as veydus_migrate
    migrate_url = (
        f"postgresql+psycopg2://{MIGRATE_USER}:{MIGRATE_PASSWORD}"
        f"@{db_config['host']}:{db_config['port']}/{_PG_DB}"
    )
    os.environ["VEYDUS_MIGRATE_DB_URL"] = migrate_url

    from alembic import command
    from alembic.config import Config

    alembic_dir = str(Path(__file__).parent.parent / "alembic")
    alembic_ini = str(Path(__file__).parent.parent / "alembic.ini")
    alembic_cfg = Config(alembic_ini)
    alembic_cfg.set_main_option("script_location", alembic_dir)
    alembic_cfg.set_main_option("sqlalchemy.url", migrate_url)
    command.upgrade(alembic_cfg, "head")

    yield db_config


@pytest.fixture()
def app_conn(migrated_db):
    """Per-test connection as veydus_app (subject to RLS).

    This is the role the application uses at runtime.  It is NOT
    the table owner and does NOT have BYPASSRLS.
    """
    conn = psycopg2.connect(
        **migrated_db,
        user=APP_USER,
        password=APP_PASSWORD,
    )
    yield conn
    conn.close()


@pytest.fixture()
def migrate_conn(migrated_db):
    """Per-test connection as veydus_migrate (table owner, BYPASSRLS).

    Used to seed data and verify schema properties.  NOT used for
    tests that exercise RLS — those use app_conn.
    """
    conn = psycopg2.connect(
        **migrated_db,
        user=MIGRATE_USER,
        password=MIGRATE_PASSWORD,
    )
    yield conn
    conn.close()


@pytest.fixture()
def superuser_conn(migrated_db):
    """Per-test connection as postgres superuser.

    Used for tests that query pg_roles, pg_tables, pg_policies, etc.
    """
    conn = psycopg2.connect(
        **migrated_db,
        user=_PG_USER,
        password=_PG_PASSWORD,
    )
    yield conn
    conn.close()


@pytest.fixture()
async def setup_test_engine(migrated_db):
    """Point the application async engine to the migrated test database."""
    from veydus.config import settings
    from veydus.db.engine import dispose_engine

    old_url = settings.app_db_url
    test_url = (
        f"postgresql+asyncpg://{APP_USER}:{APP_PASSWORD}"
        f"@{migrated_db['host']}:{migrated_db['port']}/{migrated_db['dbname']}"
    )
    settings.app_db_url = test_url
    await dispose_engine()
    yield test_url
    await dispose_engine()
    settings.app_db_url = old_url
