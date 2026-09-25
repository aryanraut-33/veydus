# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Authorization Policy & Predicates
# ─────────────────────────────────────────────────────────────────
# What:  Validates RetrievalFilter construction and can_access_chunk assertions.
# How:   Constructs UserScope instances across varying departments and hierarchy levels,
#        evaluates build_retrieval_filter output, and tests boundary conditions
#        on can_access_chunk against valid, cross-tenant, cross-department,
#        and higher-level chunks.
# Why:   HLD §2.3 specifies authz/policy.py as the single chokepoint for all
#        access predicate construction in the system.
# Tools: pytest, uuid, veydus.authz (UserScope, ChunkMeta, policy functions).
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import uuid

from veydus.authz.models import ChunkMeta, UserScope
from veydus.authz.policy import build_retrieval_filter, can_access_chunk


def test_build_retrieval_filter_compiles_scope() -> None:
    """build_retrieval_filter copies org_id, department_id, and hierarchy_level."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    dept_id = uuid.uuid4()

    scope = UserScope(
        org_id=org_id,
        user_id=user_id,
        department_id=dept_id,
        hierarchy_level=3,
        role="user",
        scope_version=1,
    )

    filt = build_retrieval_filter(scope)

    assert filt.org_id == org_id
    assert filt.department_id == dept_id
    assert filt.max_hierarchy_level == 3


def test_can_access_chunk_permitted() -> None:
    """can_access_chunk returns True when org and dept match, and chunk level <= user level."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    dept_id = uuid.uuid4()

    scope = UserScope(
        org_id=org_id,
        user_id=user_id,
        department_id=dept_id,
        hierarchy_level=3,
    )

    # Same level (3)
    chunk_equal = ChunkMeta(
        chunk_id=uuid.uuid4(),
        org_id=org_id,
        department_id=dept_id,
        hierarchy_level=3,
    )
    assert can_access_chunk(scope, chunk_equal) is True

    # Lower level (1)
    chunk_lower = ChunkMeta(
        chunk_id=uuid.uuid4(),
        org_id=org_id,
        department_id=dept_id,
        hierarchy_level=1,
    )
    assert can_access_chunk(scope, chunk_lower) is True


def test_can_access_chunk_denied_higher_level() -> None:
    """can_access_chunk returns False when chunk hierarchy_level exceeds user clearance."""
    org_id = uuid.uuid4()
    dept_id = uuid.uuid4()

    scope = UserScope(
        org_id=org_id,
        user_id=uuid.uuid4(),
        department_id=dept_id,
        hierarchy_level=2,
    )

    chunk_higher = ChunkMeta(
        chunk_id=uuid.uuid4(),
        org_id=org_id,
        department_id=dept_id,
        hierarchy_level=3,
    )
    assert can_access_chunk(scope, chunk_higher) is False


def test_can_access_chunk_denied_cross_department() -> None:
    """can_access_chunk returns False when department_id does not match."""
    org_id = uuid.uuid4()

    scope = UserScope(
        org_id=org_id,
        user_id=uuid.uuid4(),
        department_id=uuid.uuid4(),
        hierarchy_level=4,
    )

    chunk_other_dept = ChunkMeta(
        chunk_id=uuid.uuid4(),
        org_id=org_id,
        department_id=uuid.uuid4(),
        hierarchy_level=1,
    )
    assert can_access_chunk(scope, chunk_other_dept) is False


def test_can_access_chunk_denied_cross_tenant() -> None:
    """can_access_chunk returns False when org_id does not match."""
    dept_id = uuid.uuid4()

    scope = UserScope(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        department_id=dept_id,
        hierarchy_level=4,
    )

    chunk_other_org = ChunkMeta(
        chunk_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        department_id=dept_id,
        hierarchy_level=1,
    )
    assert can_access_chunk(scope, chunk_other_org) is False
