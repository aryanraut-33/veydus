# ─────────────────────────────────────────────────────────────────
# VEYDUS — Server-Side Scope Resolution & 30s TTL Cache [SECURITY-CRITICAL]
# ─────────────────────────────────────────────────────────────────
# What:  Resolves caller identity (idp_subject) into authoritative server-side
#        UserScope with an in-process 30-second TTL cache (HLD §6.2).
# How:   - Queries PostgreSQL tables (users, access_grants) for active tenant bindings:
#          org_id, user_id, department_id, hierarchy_level, role, scope_version.
#        - Maintains an in-memory cachetools.TTLCache(maxsize=10000, ttl=30)
#          keyed on idp_subject to prevent database query amplification.
#        - Provides invalidate_scope_cache() for immediate eviction upon grant changes.
#        - Fails closed with UserScopeNotFoundError if user is revoked, pending,
#          or has no valid access grant.
# Why:   HLD §6.2 & §5.2 mandate: Authorization scope must NEVER originate from client
#        claims or tokens. All hierarchical access boundaries must be verified directly
#        from database ground truth.
# Tools: cachetools.TTLCache, sqlalchemy, asyncpg, uuid.UUID, veydus.authz.models.UserScope.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from cachetools import TTLCache

if TYPE_CHECKING:
    from uuid import UUID
from sqlalchemy import text

from veydus.authz.models import UserScope

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

logger = logging.getLogger(__name__)

# HLD §6.2: 30-second in-process TTL cache for resolved scopes
_scope_cache: TTLCache[str, UserScope] = TTLCache(maxsize=10000, ttl=30)


class UserScopeNotFoundError(Exception):
    """Raised when an idp_subject has no active user account or access grant."""


async def resolve_user_scope_from_db(
    conn: AsyncConnection,
    idp_subject: str,
    bypass_cache: bool = False,
) -> UserScope:
    """Resolves an Identity Platform UID (idp_subject) into a server-validated UserScope.

    Uses an in-process 30-second TTL cache to bound database latency.
    """
    if not idp_subject:
        raise UserScopeNotFoundError("Empty idp_subject provided for scope resolution")

    # 1. Check TTL cache
    if not bypass_cache and idp_subject in _scope_cache:
        logger.debug("Scope cache hit for idp_subject: %s", idp_subject)
        return _scope_cache[idp_subject]

    # 2. Query database for active user and associated access grant
    # [SECURITY-CRITICAL] Exactly one access grant per user enforced by schema (HLD §5.2).
    query = text(
        """
        SELECT u.id AS user_id,
               u.org_id,
               u.role,
               u.scope_version,
               g.department_id,
               g.hierarchy_level
        FROM users u
        INNER JOIN access_grants g
            ON g.user_id = u.id AND g.org_id = u.org_id
        WHERE u.idp_subject = :idp_subject
          AND u.status = 'active'
        LIMIT 1
        """
    )
    result = await conn.execute(query, {"idp_subject": idp_subject})
    row = result.fetchone()

    if not row:
        logger.warning(
            "Access denied: No active user or access grant found for idp_subject: %s",
            idp_subject,
        )
        raise UserScopeNotFoundError(
            f"Active access grant not found for user identity '{idp_subject}'"
        )

    # 3. Construct validated UserScope
    scope = UserScope(
        org_id=row.org_id,
        user_id=row.user_id,
        department_id=row.department_id,
        hierarchy_level=int(row.hierarchy_level),
        role=row.role or "user",
        scope_version=int(row.scope_version or 1),
    )

    # 4. Populate cache
    _scope_cache[idp_subject] = scope
    return scope


def invalidate_scope_cache(idp_subject: str | None = None, user_id: UUID | None = None) -> None:
    """Evicts entries from the in-process scope cache.

    Called immediately when an access grant or user status changes (HLD §6.2).
    """
    if idp_subject and idp_subject in _scope_cache:
        del _scope_cache[idp_subject]
        logger.info("Evicted scope cache for idp_subject: %s", idp_subject)
    elif user_id:
        # Find matching cached scope by user_id
        keys_to_delete = [k for k, scope in _scope_cache.items() if scope.user_id == user_id]
        for k in keys_to_delete:
            del _scope_cache[k]
        logger.info("Evicted %d scope cache entries for user_id: %s", len(keys_to_delete), user_id)
    else:
        _scope_cache.clear()
        logger.info("Cleared entire in-process scope cache")


def get_cached_scope(idp_subject: str) -> UserScope | None:
    """Retrieves cached scope if present (used for testing and inspection)."""
    return _scope_cache.get(idp_subject)
