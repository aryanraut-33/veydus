# ─────────────────────────────────────────────────────────────────
# VEYDUS — Role Ownership Test  (HLD §5.4)
# ─────────────────────────────────────────────────────────────────
# What:  Asserts that veydus_app is NOT the owner of any tenant-
#        scoped table and does NOT hold BYPASSRLS.
# How:   Queries pg_tables and pg_roles (PostgreSQL system catalogs)
#        to verify ownership and role attributes.
# Why:   Table owners bypass RLS by default unless FORCE ROW LEVEL
#        SECURITY is set.  Even with FORCE, a role with BYPASSRLS
#        overrides it.  Both conditions must not apply to the
#        runtime role.
# Ref:   Implementation plan A1 success check 5.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import pytest

# All tables that carry tenant-scoped data
_ALL_TABLES = [
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


@pytest.mark.isolation
class TestOwnership:
    """veydus_app must not own tables or hold BYPASSRLS."""

    def test_veydus_app_owns_no_tables(self, superuser_conn, migrated_db):
        """No tenant-scoped table should be owned by veydus_app.

        If veydus_app owned a table, it would bypass RLS on that
        table by default (PostgreSQL behaviour), even with FORCE
        ROW LEVEL SECURITY.  Only BYPASSRLS overrides FORCE, and
        ownership does not, but it's still a violation of the
        HLD §5.4 design.
        """
        with superuser_conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename, tableowner
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tableowner = 'veydus_app'
                """
            )
            owned_tables = cur.fetchall()

        assert len(owned_tables) == 0, (
            f"veydus_app owns {len(owned_tables)} table(s): "
            f"{[t[0] for t in owned_tables]}. "
            f"The application role must not be a table owner (HLD §5.4)."
        )

    def test_veydus_app_does_not_have_bypassrls(self, superuser_conn, migrated_db):
        """veydus_app must not have the BYPASSRLS attribute.

        BYPASSRLS overrides all RLS policies, including FORCE.
        If the runtime role had it, every RLS policy would be
        decoration.
        """
        with superuser_conn.cursor() as cur:
            cur.execute(
                """
                SELECT rolbypassrls
                FROM pg_roles
                WHERE rolname = 'veydus_app'
                """
            )
            result = cur.fetchone()

        assert result is not None, "veydus_app role does not exist"
        assert result[0] is False, (
            "veydus_app has BYPASSRLS=true. This disables all RLS policies. "
            "The application role must NEVER have BYPASSRLS."
        )

    def test_all_tables_owned_by_veydus_migrate(self, superuser_conn, migrated_db):
        """Every table should be owned by veydus_migrate (the migration role).

        This is the expected state: tables created by veydus_migrate
        during Alembic migrations are owned by veydus_migrate.
        """
        with superuser_conn.cursor() as cur:
            for table in _ALL_TABLES:
                cur.execute(
                    """
                    SELECT tableowner
                    FROM pg_tables
                    WHERE schemaname = 'public' AND tablename = %s
                    """,
                    (table,),
                )
                result = cur.fetchone()
                assert result is not None, f"Table '{table}' not found"
                assert result[0] == "veydus_migrate", (
                    f"Table '{table}' owned by '{result[0]}', expected 'veydus_migrate'"
                )
