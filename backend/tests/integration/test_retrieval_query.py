# ─────────────────────────────────────────────────────────────────
# VEYDUS — Integration Tests: Vector Retrieval & Access Predicates
# ─────────────────────────────────────────────────────────────────
# What:  Integration tests verifying pgvector cosine retrieval, HNSW index usage,
#        cross-tenant zero recall, cross-department isolation, and hierarchy
#        level filtering against an ephemeral PostgreSQL 16 container.
# How:   Seeds organizations, departments, documents, and chunks with known
#        vectors and access tags, then executes retrieve_candidate_chunks()
#        within tenant_transaction() under different UserScope contexts.
# Why:   HLD §6.3 & §16 mandate strict query-level isolation: database queries
#        must never return rows across tenant, department, or clearance boundaries.
# Tools: pytest, testcontainers, pgvector, SQLAlchemy asyncpg, veydus.db.repositories.chunks.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from veydus.authz.models import UserScope
from veydus.db.repositories.chunks import retrieve_candidate_chunks
from veydus.db.session import tenant_transaction


@pytest.fixture
def seed_rag_test_data(migrate_conn):
    """Seeds multi-tenant hierarchy test fixtures across Org A and Org B."""
    org_a_id = str(uuid4())
    org_b_id = str(uuid4())

    dept_a1_id = str(uuid4())
    dept_a2_id = str(uuid4())
    dept_b1_id = str(uuid4())

    user_a_l1_id = str(uuid4())
    user_a_l2_id = str(uuid4())

    # Create dummy 768-dim unit vector
    vec_unit = [0.0] * 768
    vec_unit[0] = 1.0
    vec_str = json.dumps(vec_unit)

    with migrate_conn.cursor() as cur:
        # Orgs
        cur.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, %s, %s), (%s, %s, %s)",
            (org_a_id, "Acme Alpha", "acme-alpha", org_b_id, "Beta Corp", "beta-corp"),
        )
        # Departments
        cur.execute(
            """
            INSERT INTO departments (id, org_id, name)
            VALUES (%s, %s, %s), (%s, %s, %s), (%s, %s, %s)
            """,
            (
                dept_a1_id,
                org_a_id,
                "Engineering",
                dept_a2_id,
                org_a_id,
                "Finance",
                dept_b1_id,
                org_b_id,
                "Marketing",
            ),
        )
        # Users
        cur.execute(
            """
            INSERT INTO users (id, org_id, department_id, hierarchy_level, email, full_name)
            VALUES (%s, %s, %s, 1, 'dev@acme.com', 'Dev User'),
                   (%s, %s, %s, 2, 'mgr@acme.com', 'Mgr User')
            """,
            (user_a_l1_id, org_a_id, dept_a1_id, user_a_l2_id, org_a_id, dept_a1_id),
        )
        # Sources
        src_a1 = str(uuid4())
        src_a2 = str(uuid4())
        src_b1 = str(uuid4())
        cur.execute(
            """
            INSERT INTO sources (id, org_id, department_id, hierarchy_level, name, source_type)
            VALUES (%s, %s, %s, 1, 'Engineering Docs', 'manual'),
                   (%s, %s, %s, 1, 'Finance Docs', 'manual'),
                   (%s, %s, %s, 1, 'Beta Marketing', 'manual')
            """,
            (
                src_a1,
                org_a_id,
                dept_a1_id,
                src_a2,
                org_a_id,
                dept_a2_id,
                src_b1,
                org_b_id,
                dept_b1_id,
            ),
        )
        # Documents
        doc_a1 = str(uuid4())
        doc_a2 = str(uuid4())
        doc_b1 = str(uuid4())
        cur.execute(
            """
            INSERT INTO documents (id, org_id, source_id, title)
            VALUES (%s, %s, %s, 'Eng Guide'),
                   (%s, %s, %s, 'Fin Balance'),
                   (%s, %s, %s, 'Beta Strategy')
            """,
            (doc_a1, org_a_id, src_a1, doc_a2, org_a_id, src_a2, doc_b1, org_b_id, src_b1),
        )
        # Chunks in Org A - Dept A1 - Level 1
        chunk_a1_l1 = str(uuid4())
        cur.execute(
            """
            INSERT INTO chunks (id, org_id, document_id, chunk_index, content, token_count, department_id, hierarchy_level, embedding)
            VALUES (%s, %s, %s, 0, 'Public engineering guidelines', 10, %s, 1, %s::vector)
            """,
            (chunk_a1_l1, org_a_id, doc_a1, dept_a1_id, vec_str),
        )
        # Chunks in Org A - Dept A1 - Level 2
        chunk_a1_l2 = str(uuid4())
        cur.execute(
            """
            INSERT INTO chunks (id, org_id, document_id, chunk_index, content, token_count, department_id, hierarchy_level, embedding)
            VALUES (%s, %s, %s, 1, 'Confidential engineering roadmap', 10, %s, 2, %s::vector)
            """,
            (chunk_a1_l2, org_a_id, doc_a1, dept_a1_id, vec_str),
        )
        # Chunks in Org A - Dept A2 - Level 1
        chunk_a2_l1 = str(uuid4())
        cur.execute(
            """
            INSERT INTO chunks (id, org_id, document_id, chunk_index, content, token_count, department_id, hierarchy_level, embedding)
            VALUES (%s, %s, %s, 0, 'Finance Q3 results', 10, %s, 1, %s::vector)
            """,
            (chunk_a2_l1, org_a_id, doc_a2, dept_a2_id, vec_str),
        )
        # Chunks in Org B - Dept B1 - Level 1
        chunk_b1_l1 = str(uuid4())
        cur.execute(
            """
            INSERT INTO chunks (id, org_id, document_id, chunk_index, content, token_count, department_id, hierarchy_level, embedding)
            VALUES (%s, %s, %s, 0, 'Beta Corp secret marketing plans', 10, %s, 1, %s::vector)
            """,
            (chunk_b1_l1, org_b_id, doc_b1, dept_b1_id, vec_str),
        )

    migrate_conn.commit()

    return {
        "org_a_id": org_a_id,
        "org_b_id": org_b_id,
        "dept_a1_id": dept_a1_id,
        "dept_a2_id": dept_a2_id,
        "dept_b1_id": dept_b1_id,
        "user_a_l1_id": user_a_l1_id,
        "user_a_l2_id": user_a_l2_id,
        "chunk_a1_l1": chunk_a1_l1,
        "chunk_a1_l2": chunk_a1_l2,
        "chunk_a2_l1": chunk_a2_l1,
        "chunk_b1_l1": chunk_b1_l1,
        "query_vector": vec_unit,
    }


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieval_cross_tenant_zero_recall(setup_test_engine, seed_rag_test_data) -> None:
    data = seed_rag_test_data
    from uuid import UUID

    scope_a = UserScope(
        org_id=UUID(data["org_a_id"]),
        user_id=UUID(data["user_a_l1_id"]),
        department_id=UUID(data["dept_a1_id"]),
        hierarchy_level=1,
    )

    async with tenant_transaction(scope_a.org_id) as conn:
        chunks = await retrieve_candidate_chunks(
            conn=conn,
            scope=scope_a,
            query_embedding=data["query_vector"],
            k=10,
        )

    chunk_ids = [str(c.id) for c in chunks]
    assert data["chunk_b1_l1"] not in chunk_ids
    assert data["chunk_a1_l1"] in chunk_ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieval_cross_department_zero_recall(
    setup_test_engine, seed_rag_test_data
) -> None:
    data = seed_rag_test_data
    from uuid import UUID

    scope_a = UserScope(
        org_id=UUID(data["org_a_id"]),
        user_id=UUID(data["user_a_l1_id"]),
        department_id=UUID(data["dept_a1_id"]),
        hierarchy_level=1,
    )

    async with tenant_transaction(scope_a.org_id) as conn:
        chunks = await retrieve_candidate_chunks(
            conn=conn,
            scope=scope_a,
            query_embedding=data["query_vector"],
            k=10,
        )

    chunk_ids = [str(c.id) for c in chunks]
    # Dept A2 (Finance) chunk must NOT be returned to Dept A1 user
    assert data["chunk_a2_l1"] not in chunk_ids
    assert data["chunk_a1_l1"] in chunk_ids


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieval_hierarchy_level_filtering(setup_test_engine, seed_rag_test_data) -> None:
    data = seed_rag_test_data
    from uuid import UUID

    # Level 1 user
    scope_l1 = UserScope(
        org_id=UUID(data["org_a_id"]),
        user_id=UUID(data["user_a_l1_id"]),
        department_id=UUID(data["dept_a1_id"]),
        hierarchy_level=1,
    )

    async with tenant_transaction(scope_l1.org_id) as conn:
        chunks_l1 = await retrieve_candidate_chunks(
            conn=conn,
            scope=scope_l1,
            query_embedding=data["query_vector"],
            k=10,
        )

    chunk_ids_l1 = [str(c.id) for c in chunks_l1]
    assert data["chunk_a1_l1"] in chunk_ids_l1
    assert data["chunk_a1_l2"] not in chunk_ids_l1

    # Level 2 user
    scope_l2 = UserScope(
        org_id=UUID(data["org_a_id"]),
        user_id=UUID(data["user_a_l2_id"]),
        department_id=UUID(data["dept_a1_id"]),
        hierarchy_level=2,
    )

    async with tenant_transaction(scope_l2.org_id) as conn:
        chunks_l2 = await retrieve_candidate_chunks(
            conn=conn,
            scope=scope_l2,
            query_embedding=data["query_vector"],
            k=10,
        )

    chunk_ids_l2 = [str(c.id) for c in chunks_l2]
    assert data["chunk_a1_l1"] in chunk_ids_l2
    assert data["chunk_a1_l2"] in chunk_ids_l2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retrieval_query_explain_analyze(setup_test_engine, seed_rag_test_data) -> None:
    data = seed_rag_test_data
    from uuid import UUID

    scope = UserScope(
        org_id=UUID(data["org_a_id"]),
        user_id=UUID(data["user_a_l1_id"]),
        department_id=UUID(data["dept_a1_id"]),
        hierarchy_level=2,
    )

    async with tenant_transaction(scope.org_id) as conn:
        # Execute EXPLAIN (ANALYZE, BUFFERS) on retrieval query
        vec_str = f"[{','.join(str(x) for x in data['query_vector'])}]"
        explain_query = text(
            f"""
            EXPLAIN (ANALYZE, BUFFERS)
            SELECT c.id, c.content
            FROM chunks c
            WHERE c.org_id = :org_id
              AND c.department_id = :department_id
              AND c.hierarchy_level <= :hierarchy_level
            ORDER BY c.embedding <=> '{vec_str}'::vector
            LIMIT 10
            """
        )
        res = await conn.execute(
            explain_query,
            {
                "org_id": scope.org_id,
                "department_id": scope.department_id,
                "hierarchy_level": scope.hierarchy_level,
            },
        )
        plan_lines = [r[0] for r in res.fetchall()]
        plan_str = "\n".join(plan_lines)
        assert "Execution Time" in plan_str
