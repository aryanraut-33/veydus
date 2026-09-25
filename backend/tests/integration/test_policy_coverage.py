# ─────────────────────────────────────────────────────────────────
# VEYDUS — RLS Policy Coverage CI Check  (HLD §18.2)
# ─────────────────────────────────────────────────────────────────
# What:  Enumerates all tenant-scoped tables and fails the build if
#        any lacks BOTH 'ENABLE ROW LEVEL SECURITY' and 'FORCE ROW
#        LEVEL SECURITY', or if any lacks a tenant_isolation policy.
# How:   Queries pg_class (for RLS flags) and pg_policies (for
#        policy existence) from the PostgreSQL system catalogs.
# Why:   A new table added without RLS in a future migration would
#        silently bypass tenant isolation.  This test catches it at
#        CI time rather than in production.
# Ref:   Implementation plan A1 success check 7 (CI policy-coverage
#        check is proven, not assumed).
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import pytest

# Every table in the public schema that must have RLS.
# This list must be updated when new tables are added — and this
# test will FAIL if a table exists without RLS, which is the point.
_EXPECTED_TABLES = [
    "organizations",
    "departments",
    "users",
    "access_grants",
    "access_requests",
    "sources",
    "documents",
    "chunks",
    "conversations",
    "messages",
    "session_chunks",
    "org_settings",
    "ingestion_jobs",
    "audit_log",
]


@pytest.mark.integration
class TestPolicyCoverage:
    """Every tenant-scoped table must have RLS enabled, forced, and a policy."""

    def test_all_tables_have_rls_enabled(self, superuser_conn, migrated_db):
        """Every table must have ROW LEVEL SECURITY enabled.

        Checks pg_class.relrowsecurity for each table.
        """
        missing = []
        with superuser_conn.cursor() as cur:
            for table in _EXPECTED_TABLES:
                cur.execute(
                    """
                    SELECT relrowsecurity
                    FROM pg_class
                    WHERE relname = %s AND relnamespace = 'public'::regnamespace
                    """,
                    (table,),
                )
                result = cur.fetchone()
                if result is None:
                    missing.append(f"{table} (TABLE NOT FOUND)")
                elif not result[0]:
                    missing.append(f"{table} (ENABLE ROW LEVEL SECURITY missing)")

        assert len(missing) == 0, f"RLS not enabled on {len(missing)} table(s): {missing}"

    def test_all_tables_have_rls_forced(self, superuser_conn, migrated_db):
        """Every table must have FORCE ROW LEVEL SECURITY set.

        FORCE makes RLS apply even to the table owner (defence in depth).
        Checks pg_class.relforcerowsecurity.
        """
        missing = []
        with superuser_conn.cursor() as cur:
            for table in _EXPECTED_TABLES:
                cur.execute(
                    """
                    SELECT relforcerowsecurity
                    FROM pg_class
                    WHERE relname = %s AND relnamespace = 'public'::regnamespace
                    """,
                    (table,),
                )
                result = cur.fetchone()
                if result is None:
                    missing.append(f"{table} (TABLE NOT FOUND)")
                elif not result[0]:
                    missing.append(f"{table} (FORCE ROW LEVEL SECURITY missing)")

        assert len(missing) == 0, f"FORCE RLS not set on {len(missing)} table(s): {missing}"

    def test_all_tables_have_tenant_isolation_policy(self, superuser_conn, migrated_db):
        """Every table must have a 'tenant_isolation' policy.

        This catches the case where ENABLE/FORCE are set but no
        policy exists — RLS with no policies denies ALL access,
        which is safe but breaks the application.
        """
        missing = []
        with superuser_conn.cursor() as cur:
            for table in _EXPECTED_TABLES:
                cur.execute(
                    """
                    SELECT count(*)
                    FROM pg_policies
                    WHERE tablename = %s
                      AND schemaname = 'public'
                      AND policyname = 'tenant_isolation'
                    """,
                    (table,),
                )
                count = cur.fetchone()[0]
                if count == 0:
                    missing.append(table)

        assert len(missing) == 0, (
            f"tenant_isolation policy missing on {len(missing)} table(s): {missing}"
        )

    def test_no_public_tables_without_rls(self, superuser_conn, migrated_db):
        """No table in the public schema should exist without RLS.

        This catches tables added by future migrations that forget
        to add RLS.  It's stricter than the above tests — those
        check a known list, this checks ALL tables.
        """
        with superuser_conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND NOT c.relrowsecurity
                  AND c.relname NOT LIKE 'alembic_%'
                """
            )
            unprotected = [row[0] for row in cur.fetchall()]

        assert len(unprotected) == 0, (
            f"Tables without RLS in public schema: {unprotected}. "
            f"Every table must have ENABLE ROW LEVEL SECURITY."
        )

    def test_policy_coverage_fails_on_unprotected_table(self, superuser_conn, migrated_db):
        """Prove CI fails if a table is created without RLS (Implementation Plan A1 Success Check 7)."""
        with superuser_conn.cursor() as cur:
            cur.execute("CREATE TABLE scratch_unprotected_test (id serial primary key)")
        try:
            with pytest.raises(AssertionError, match="Tables without RLS"):
                self.test_no_public_tables_without_rls(superuser_conn, migrated_db)
        finally:
            with superuser_conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS scratch_unprotected_test")
