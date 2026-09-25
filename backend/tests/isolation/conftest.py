# ─────────────────────────────────────────────────────────────────
# VEYDUS — Isolation Test Fixtures
# ─────────────────────────────────────────────────────────────────
# What:  Shared fixtures for the adversarial isolation test suite.
#        Seeds two orgs with chunks containing unique canary strings
#        so RLS and access-filter tests have data to work with.
# How:   Inserts as veydus_migrate (BYPASSRLS) so seeding is not
#        subject to RLS.  Each chunk gets a random 768-dim embedding
#        (not meaningful for similarity search, but sufficient for
#        testing RLS which operates on org_id/department_id/level).
# Why:   Every isolation test needs at least one row in chunks to
#        distinguish "zero rows because RLS blocked it" from "zero
#        rows because the table is empty."  The positive-control
#        pattern (check 5 in the plan) depends on this.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import random
from typing import Any

import psycopg2
import pytest

from tests.conftest import MIGRATE_PASSWORD, MIGRATE_USER


def _random_embedding(dims: int = 768) -> str:
    """Generate a random vector literal for pgvector.

    Format: '[0.1, -0.3, ...]' — the text representation pgvector accepts.
    Not meaningful for similarity, but valid for schema compliance.
    """
    components = [str(round(random.uniform(-1, 1), 6)) for _ in range(dims)]
    return "[" + ",".join(components) + "]"


@pytest.fixture(scope="session")
def seeded_orgs(migrated_db):
    """Seed two orgs with chunks containing unique canary strings.

    Returns a dict with all IDs needed by isolation tests:
        {
            "org_a": { "id", "dept_finance", "dept_engineering",
                       "operator", "user", "chunk_finance_l1",
                       "chunk_finance_l3", "canary_finance_l1",
                       "canary_finance_l3" },
            "org_b": { "id", "dept_hr", "operator", "user",
                       "chunk_hr_l1", "canary_hr_l1" },
        }
    """
    conn = psycopg2.connect(
        **migrated_db,
        user=MIGRATE_USER,
        password=MIGRATE_PASSWORD,
    )
    conn.autocommit = False

    result: dict[str, dict[str, Any]] = {"org_a": {}, "org_b": {}}

    try:
        with conn, conn.cursor() as cur:
            # ── Org A: Acme Corp ─────────────────────────────
            cur.execute(
                "INSERT INTO organizations (name, slug) VALUES ('Acme Corp', 'acme-test') RETURNING id"
            )
            org_a_id = cur.fetchone()[0]
            result["org_a"]["id"] = org_a_id

            cur.execute("INSERT INTO org_settings (org_id) VALUES (%s)", (org_a_id,))

            # Departments
            cur.execute(
                "INSERT INTO departments (org_id, name, max_level) VALUES (%s, 'Finance', 3) RETURNING id",
                (org_a_id,),
            )
            dept_finance = cur.fetchone()[0]
            result["org_a"]["dept_finance"] = dept_finance

            cur.execute(
                "INSERT INTO departments (org_id, name, max_level) VALUES (%s, 'Engineering', 3) RETURNING id",
                (org_a_id,),
            )
            result["org_a"]["dept_engineering"] = cur.fetchone()[0]

            # Operator
            cur.execute(
                """INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                       VALUES (%s, 'test-op-acme', 'test-op@acme.com', 'Test Op', 'operator', 'active')
                       RETURNING id""",
                (org_a_id,),
            )
            operator_a = cur.fetchone()[0]
            result["org_a"]["operator"] = operator_a

            # User with Finance L2 access
            cur.execute(
                """INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                       VALUES (%s, 'test-user-acme', 'test-user@acme.com', 'Test User', 'user', 'active')
                       RETURNING id""",
                (org_a_id,),
            )
            user_a = cur.fetchone()[0]
            result["org_a"]["user"] = user_a

            cur.execute(
                """INSERT INTO access_grants (user_id, org_id, department_id, hierarchy_level, granted_by)
                       VALUES (%s, %s, %s, 2, %s)""",
                (user_a, org_a_id, dept_finance, operator_a),
            )

            # Source + document (needed as FK parents for chunks)
            cur.execute(
                """INSERT INTO sources (org_id, kind, display_name, department_id, hierarchy_level, status, created_by)
                       VALUES (%s, 'upload', 'test-source-acme', %s, 3, 'ready', %s)
                       RETURNING id""",
                (org_a_id, dept_finance, operator_a),
            )
            source_a = cur.fetchone()[0]

            cur.execute(
                """INSERT INTO documents (org_id, source_id, title, mime_type, content_hash)
                       VALUES (%s, %s, 'test-doc-acme', 'text/plain', 'sha256-acme-test')
                       RETURNING id""",
                (org_a_id, source_a),
            )
            doc_a = cur.fetchone()[0]

            # Chunks with canary strings
            canary_fin_l1 = "CANARY-ACME-FINANCE-L1-7x9k2m"
            canary_fin_l3 = "CANARY-ACME-FINANCE-L3-p4w8nq"
            result["org_a"]["canary_finance_l1"] = canary_fin_l1
            result["org_a"]["canary_finance_l3"] = canary_fin_l3

            cur.execute(
                """INSERT INTO chunks (org_id, document_id, source_id, department_id, hierarchy_level,
                                          ordinal, content, token_count, embedding)
                       VALUES (%s, %s, %s, %s, 1, 1, %s, 10, %s::vector)
                       RETURNING id""",
                (org_a_id, doc_a, source_a, dept_finance, canary_fin_l1, _random_embedding()),
            )
            result["org_a"]["chunk_finance_l1"] = cur.fetchone()[0]

            cur.execute(
                """INSERT INTO chunks (org_id, document_id, source_id, department_id, hierarchy_level,
                                          ordinal, content, token_count, embedding)
                       VALUES (%s, %s, %s, %s, 3, 2, %s, 10, %s::vector)
                       RETURNING id""",
                (org_a_id, doc_a, source_a, dept_finance, canary_fin_l3, _random_embedding()),
            )
            result["org_a"]["chunk_finance_l3"] = cur.fetchone()[0]

            # ── Org B: Globex Inc ────────────────────────────
            cur.execute(
                "INSERT INTO organizations (name, slug) VALUES ('Globex Inc', 'globex-test') RETURNING id"
            )
            org_b_id = cur.fetchone()[0]
            result["org_b"]["id"] = org_b_id

            cur.execute("INSERT INTO org_settings (org_id) VALUES (%s)", (org_b_id,))

            cur.execute(
                "INSERT INTO departments (org_id, name, max_level) VALUES (%s, 'HR', 3) RETURNING id",
                (org_b_id,),
            )
            dept_hr = cur.fetchone()[0]
            result["org_b"]["dept_hr"] = dept_hr

            cur.execute(
                """INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                       VALUES (%s, 'test-op-globex', 'test-op@globex.com', 'Test Op', 'operator', 'active')
                       RETURNING id""",
                (org_b_id,),
            )
            operator_b = cur.fetchone()[0]
            result["org_b"]["operator"] = operator_b

            cur.execute(
                """INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                       VALUES (%s, 'test-user-globex', 'test-user@globex.com', 'Test User', 'user', 'active')
                       RETURNING id""",
                (org_b_id,),
            )
            user_b = cur.fetchone()[0]
            result["org_b"]["user"] = user_b

            cur.execute(
                """INSERT INTO access_grants (user_id, org_id, department_id, hierarchy_level, granted_by)
                       VALUES (%s, %s, %s, 1, %s)""",
                (user_b, org_b_id, dept_hr, operator_b),
            )

            # Source + document + chunk for Org B
            cur.execute(
                """INSERT INTO sources (org_id, kind, display_name, department_id, hierarchy_level, status, created_by)
                       VALUES (%s, 'upload', 'test-source-globex', %s, 1, 'ready', %s)
                       RETURNING id""",
                (org_b_id, dept_hr, operator_b),
            )
            source_b = cur.fetchone()[0]

            cur.execute(
                """INSERT INTO documents (org_id, source_id, title, mime_type, content_hash)
                       VALUES (%s, %s, 'test-doc-globex', 'text/plain', 'sha256-globex-test')
                       RETURNING id""",
                (org_b_id, source_b),
            )
            doc_b = cur.fetchone()[0]

            canary_hr_l1 = "CANARY-GLOBEX-HR-L1-j3m6yx"
            result["org_b"]["canary_hr_l1"] = canary_hr_l1

            cur.execute(
                """INSERT INTO chunks (org_id, document_id, source_id, department_id, hierarchy_level,
                                          ordinal, content, token_count, embedding)
                       VALUES (%s, %s, %s, %s, 1, 1, %s, 10, %s::vector)
                       RETURNING id""",
                (org_b_id, doc_b, source_b, dept_hr, canary_hr_l1, _random_embedding()),
            )
            result["org_b"]["chunk_hr_l1"] = cur.fetchone()[0]

    finally:
        conn.close()

    return result
