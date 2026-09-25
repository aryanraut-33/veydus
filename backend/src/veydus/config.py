# ─────────────────────────────────────────────────────────────────
# VEYDUS — Application Configuration
# ─────────────────────────────────────────────────────────────────
# What:  Centralised settings loaded from environment variables.
# How:   Uses pydantic-settings to read env vars with type
#        validation, defaults for local dev, and a .env file
#        fallback.  Every configurable value in the system flows
#        through this module — no scattered os.getenv() calls.
# Why:   HLD §7.4 marks several values as "tunable" (chunk size,
#        retrieval k, ef_search, etc.).  Centralising them here
#        makes tuning a config change, not a code change.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, populated from environment variables.

    Every field maps to an env var with the same name (case-insensitive).
    For example, ``VEYDUS_APP_DB_URL`` maps to ``veydus_app_db_url``.
    """

    model_config = SettingsConfigDict(
        env_prefix="VEYDUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ─────────────────────────────────────────────────
    # Runtime connection (veydus_app role, subject to RLS)
    app_db_url: str = "postgresql+asyncpg://veydus_app:veydus_app_pw@localhost:5432/veydus"
    # Migration connection (veydus_migrate role, BYPASSRLS)
    migrate_db_url: str = "postgresql://veydus_migrate:veydus_migrate_pw@localhost:5432/veydus"

    # ── Connection Pool ──────────────────────────────────────────
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ── Application ──────────────────────────────────────────────
    debug: bool = False
    log_level: str = "INFO"


# Module-level singleton — import this, don't instantiate Settings again.
settings = Settings()
