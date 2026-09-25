# ─────────────────────────────────────────────────────────────────
# VEYDUS — Tenant-Scoped Database Session   [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  The single chokepoint for all tenant-scoped database
#        access.  Every repository call goes through this context
#        manager — no exceptions.
#
# How:   Opens a transaction, issues SET LOCAL veydus.org_id to
#        activate the RLS policy for the request's organization,
#        yields a connection, and commits on clean exit (or rolls
#        back on error).
#
# Why SET LOCAL and not SET:
#        SET persists for the life of the database session (i.e.
#        the pooled connection).  If a connection carrying a stale
#        org_id is handed to a request from a different org, that
#        request silently sees the wrong tenant's data — a cross-
#        tenant leak with no error and no log anomaly.
#
#        SET LOCAL is transaction-scoped: it is automatically
#        discarded on COMMIT or ROLLBACK.  When the context manager
#        exits, the transaction ends and the connection is returned
#        to the pool with NO residual org_id.
#
#        A bare SET (without LOCAL) in application code is a lint-
#        level prohibition (HLD §5.4).
#
# Ref:   HLD §5.4 — "The connection pooling hazard"
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

from veydus.db.engine import get_engine

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncGenerator

    from sqlalchemy.ext.asyncio import AsyncConnection


@asynccontextmanager
async def tenant_transaction(
    org_id: uuid.UUID,
) -> AsyncGenerator[AsyncConnection, None]:
    """Execute a block of work within a tenant-scoped transaction.

    Usage::

        async with tenant_transaction(org_id) as conn:
            result = await conn.execute(text("SELECT ..."))

    Guarantees:
      1. ``SET LOCAL veydus.org_id`` is issued before the caller's
         code runs, activating RLS for this organization.
      2. On clean exit the transaction is committed.
      3. On any exception the transaction is rolled back.
      4. After exit (commit or rollback), the connection is returned
         to the pool with NO residual ``veydus.org_id``.

    Args:
        org_id: The UUID of the organization for this request.
                Must come from server-side scope resolution, NEVER
                from request input (HLD §6.2).
    """
    engine = get_engine()

    async with engine.begin() as conn:
        # SET LOCAL is transaction-scoped — discarded on commit/rollback.
        # This is the ONLY place in the codebase where veydus.org_id
        # is set.  Any other SET statement is a defect.
        await conn.execute(
            text("SET LOCAL veydus.org_id = :org_id"),
            {"org_id": str(org_id)},
        )
        yield conn
        # engine.begin() auto-commits here on clean exit,
        # or auto-rolls-back if the caller raised an exception.
        # Either way, SET LOCAL is discarded and the connection
        # is returned to the pool clean.
