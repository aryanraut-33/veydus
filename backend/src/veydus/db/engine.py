# ─────────────────────────────────────────────────────────────────
# VEYDUS — Async SQLAlchemy Engine Factory
# ─────────────────────────────────────────────────────────────────
# What:  Creates and manages the async SQLAlchemy engine used by
#        the application at runtime.
# How:   Uses SQLAlchemy 2.x create_async_engine with the asyncpg
#        driver.  Connection pooling is configured via Settings.
# Why:   A single engine instance is shared across the application
#        to reuse the asyncpg connection pool.  The pool is the
#        reason the SET LOCAL pattern in session.py is critical —
#        connections are recycled, and any state left on them leaks
#        to the next request (HLD §5.4, pooling hazard).
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from veydus.config import settings

# The single async engine for the application.
# Created at module import time so it's available everywhere.
# The pool settings match the expected concurrency of a single
# Cloud Run instance (HLD §4.3).
_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """Return the shared async engine, creating it on first call.

    The engine uses asyncpg (PostgreSQL async driver) and a
    connection pool sized from Settings.  All connections go
    through this engine, ensuring the pool is shared.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.app_db_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            # Echo SQL in debug mode for development visibility
            echo=settings.debug,
        )
    return _engine


async def dispose_engine() -> None:
    """Cleanly shut down the connection pool.

    Called during application shutdown (FastAPI lifespan) to
    release all pooled connections.
    """
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
