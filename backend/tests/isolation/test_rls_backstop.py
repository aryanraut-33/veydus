# ─────────────────────────────────────────────────────────────────
# VEYDUS — RLS Backstop Test  (HLD §17.2, cross-tenant family)
# ─────────────────────────────────────────────────────────────────
# What:  Proves that a query as veydus_app WITHOUT SET LOCAL
#        veydus.org_id returns ZERO rows — not all rows, not an
#        error that a caller could catch and ignore.
# How:   Connects as veydus_app, queries the chunks table without
#        setting veydus.org_id, and asserts the result is empty.
# Why:   This is the last line of defence.  If RLS is misconfigured
#        or disabled, a missing SET LOCAL would silently return all
#        tenants' data.  This test catches that.
# Ref:   Implementation plan A1 success check 3.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import pytest


@pytest.mark.isolation
class TestRLSBackstop:
    """RLS must return zero rows when no tenant context is set."""

    def test_chunks_returns_zero_without_org_id(self, app_conn, seeded_orgs):
        """Query chunks as veydus_app with no SET LOCAL → zero rows.

        This is the most dangerous failure mode: a missing SET LOCAL
        should never return data.  If it returns rows, RLS is broken.
        """
        # seeded_orgs ensures there IS data in the chunks table
        with app_conn.cursor() as cur:
            # Do NOT set veydus.org_id — this is the point of the test.
            # The RLS policy requires:
            #   org_id = current_setting('veydus.org_id')::uuid
            # Without the setting, current_setting raises an error OR
            # returns NULL, and the policy rejects all rows.
            cur.execute("SELECT count(*) FROM chunks")
            count = cur.fetchone()[0]

        assert count == 0, (
            f"RLS FAILURE: chunks returned {count} rows without SET LOCAL veydus.org_id. "
            f"Expected 0. This is a cross-tenant data leak."
        )

    def test_users_returns_zero_without_org_id(self, app_conn, seeded_orgs):
        """Same backstop check on the users table."""
        with app_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM users")
            count = cur.fetchone()[0]

        assert count == 0, (
            f"RLS FAILURE: users returned {count} rows without SET LOCAL veydus.org_id."
        )

    def test_organizations_returns_zero_without_org_id(self, app_conn, seeded_orgs):
        """Backstop on organizations (uses 'id' not 'org_id' for the policy)."""
        with app_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM organizations")
            count = cur.fetchone()[0]

        assert count == 0, f"RLS FAILURE: organizations returned {count} rows without SET LOCAL."

    def test_audit_log_returns_zero_without_org_id(self, app_conn, seeded_orgs):
        """Backstop on audit_log — even read access is tenant-scoped."""
        with app_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM audit_log")
            count = cur.fetchone()[0]

        assert count == 0

    def test_with_org_id_returns_correct_data(self, app_conn, seeded_orgs):
        """Positive control: WITH SET LOCAL, the correct rows appear.

        Without this test, the backstop tests could pass on a system
        that returns nothing at all (e.g., empty GRANT).  This proves
        the data exists and is accessible when the context is set.
        """
        org_a_id = seeded_orgs["org_a"]["id"]

        with app_conn, app_conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("SET LOCAL veydus.org_id = %s", (str(org_a_id),))
            cur.execute("SELECT count(*) FROM chunks")
            count = cur.fetchone()[0]
            cur.execute("COMMIT")

        assert count > 0, (
            "Positive control failed: SET LOCAL veydus.org_id is set but chunks "
            "returned 0 rows. The test data was not seeded correctly."
        )
