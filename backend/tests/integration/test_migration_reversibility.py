# ─────────────────────────────────────────────────────────────────
# VEYDUS — Migration Reversibility Test (Sprint A1 Success Check 1)
# ─────────────────────────────────────────────────────────────────
# What:  Verifies that Alembic schema migrations can be cleanly
#        downgraded to base and upgraded back to head.
# How:   Executes alembic.command.downgrade(cfg, 'base') followed by
#        alembic.command.upgrade(cfg, 'head') using the veydus_migrate
#        role against the test database.
# Why:   While production migrations are strictly forward-only, reversibility
#        proves that all DDL statements in the initial revision are
#        complete, paired, and correctly ordered.
# Tools: pytest, alembic, pathlib
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import _PG_DB, MIGRATE_PASSWORD, MIGRATE_USER


@pytest.mark.integration
def test_migration_downgrade_and_reupgrade(
    db_config: dict[str, str | int], migrated_db: dict[str, str | int]
) -> None:
    """Verify that alembic downgrade base then upgrade head succeeds cleanly."""
    migrate_url = (
        f"postgresql+psycopg2://{MIGRATE_USER}:{MIGRATE_PASSWORD}"
        f"@{db_config['host']}:{db_config['port']}/{_PG_DB}"
    )
    os.environ["VEYDUS_MIGRATE_DB_URL"] = migrate_url

    alembic_dir = str(Path(__file__).resolve().parents[2] / "alembic")
    alembic_ini = str(Path(__file__).resolve().parents[2] / "alembic.ini")
    alembic_cfg = Config(alembic_ini)
    alembic_cfg.set_main_option("script_location", alembic_dir)
    alembic_cfg.set_main_option("sqlalchemy.url", migrate_url)

    # 1. Downgrade to base
    command.downgrade(alembic_cfg, "base")

    # 2. Upgrade back to head
    command.upgrade(alembic_cfg, "head")
