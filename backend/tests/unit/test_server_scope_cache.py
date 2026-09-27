# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Server-Side Scope Resolution & TTL Cache
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying database-driven UserScope resolution, in-process
#        30-second TTL caching, and grant-change cache invalidation (HLD §6.2).
# How:   Mocks SQLAlchemy AsyncConnection, asserts that subsequent calls for the
#        same idp_subject return cached UserScope without invoking the database,
#        verifies invalidation eviction, and checks UserScopeNotFoundError for missing users.
# Why:   HLD §6.2: Guarantees tenant isolation boundaries are resolved from
#        database ground truth while bounding database load via strict TTL caching.
# Tools: pytest, unittest.mock (AsyncMock, MagicMock), veydus.auth.scope.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from veydus.auth.scope import (
    UserScopeNotFoundError,
    get_cached_scope,
    invalidate_scope_cache,
    resolve_user_scope_from_db,
)


@pytest.fixture(autouse=True)
def clean_scope_cache():
    """Ensures cache is clean before and after each test."""
    invalidate_scope_cache()
    yield
    invalidate_scope_cache()


@pytest.mark.asyncio
async def test_resolve_user_scope_database_success() -> None:
    """Verifies that an active user and grant resolve to UserScope and populate cache."""
    idp_sub = f"idp_user_{uuid4().hex[:8]}"
    org_id = uuid4()
    user_id = uuid4()
    dept_id = uuid4()

    mock_row = MagicMock()
    mock_row.user_id = user_id
    mock_row.org_id = org_id
    mock_row.department_id = dept_id
    mock_row.hierarchy_level = 2
    mock_row.role = "user"
    mock_row.scope_version = 1

    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row

    mock_conn = AsyncMock()
    mock_conn.execute.return_value = mock_result

    # 1. First resolution: queries database
    scope = await resolve_user_scope_from_db(mock_conn, idp_sub)
    assert scope.org_id == org_id
    assert scope.user_id == user_id
    assert scope.department_id == dept_id
    assert scope.hierarchy_level == 2
    assert scope.role == "user"
    assert mock_conn.execute.call_count == 1

    # 2. Second resolution: hits TTL cache, zero additional DB queries
    cached_scope = await resolve_user_scope_from_db(mock_conn, idp_sub)
    assert cached_scope == scope
    assert mock_conn.execute.call_count == 1  # Still 1, did not query DB

    # Verify cached entry directly
    assert get_cached_scope(idp_sub) is not None


@pytest.mark.asyncio
async def test_invalidate_scope_cache_forces_db_requery() -> None:
    """Verifies that invalidate_scope_cache evicts cached entries."""
    idp_sub = f"idp_user_{uuid4().hex[:8]}"
    org_id = uuid4()
    user_id = uuid4()
    dept_id = uuid4()

    mock_row = MagicMock()
    mock_row.user_id = user_id
    mock_row.org_id = org_id
    mock_row.department_id = dept_id
    mock_row.hierarchy_level = 1
    mock_row.role = "user"
    mock_row.scope_version = 1

    mock_result = MagicMock()
    mock_result.fetchone.return_value = mock_row

    mock_conn = AsyncMock()
    mock_conn.execute.return_value = mock_result

    # Seed cache
    _ = await resolve_user_scope_from_db(mock_conn, idp_sub)
    assert mock_conn.execute.call_count == 1

    # Invalidate by idp_subject
    invalidate_scope_cache(idp_subject=idp_sub)
    assert get_cached_scope(idp_sub) is None

    # Next call must query database again
    _ = await resolve_user_scope_from_db(mock_conn, idp_sub)
    assert mock_conn.execute.call_count == 2


@pytest.mark.asyncio
async def test_resolve_user_scope_not_found_raises() -> None:
    """Verifies that non-existent or inactive user raises UserScopeNotFoundError."""
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None

    mock_conn = AsyncMock()
    mock_conn.execute.return_value = mock_result

    with pytest.raises(UserScopeNotFoundError) as exc_info:
        await resolve_user_scope_from_db(mock_conn, "unknown_idp_uid")

    assert "Active access grant not found" in str(exc_info.value)
