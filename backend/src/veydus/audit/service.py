# ─────────────────────────────────────────────────────────────────
# VEYDUS — Audit Service
# ─────────────────────────────────────────────────────────────────
# What:  Append-only audit event recording for security, retrieval tracking,
#        and compliance monitoring.
# How:   Executes parameterized SQL INSERT into audit_log table within the active
#        tenant session (RLS context).
# Why:   HLD §5.4 & §11 mandate immutable audit recording for query requests,
#        citations, out-of-scope assertion checks, and access events.
# Tools: SQLAlchemy (text, AsyncConnection), uuid.UUID, json, logging.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)


async def record_audit_event(
    conn: AsyncConnection,
    org_id: UUID,
    event_type: str,
    payload: dict[str, Any],
    actor_user_id: UUID | None = None,
    trace_id: str | None = None,
) -> int:
    """Inserts an immutable audit event record into the audit_log table."""
    stmt = text(
        """
        INSERT INTO audit_log (org_id, actor_user_id, event_type, payload, trace_id)
        VALUES (:org_id, :actor_user_id, :event_type, :payload, :trace_id)
        RETURNING id
        """
    )
    result = await conn.execute(
        stmt,
        {
            "org_id": org_id,
            "actor_user_id": actor_user_id,
            "event_type": event_type,
            "payload": json.dumps(payload),
            "trace_id": trace_id,
        },
    )
    row = result.fetchone()
    audit_id = int(row[0]) if row else 0
    logger.debug("Recorded audit event %s (id=%d) for org %s", event_type, audit_id, org_id)
    return audit_id
