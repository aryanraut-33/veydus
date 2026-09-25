# ─────────────────────────────────────────────────────────────────
# VEYDUS — Audit Log Immutability Test  (HLD §5.4)
# ─────────────────────────────────────────────────────────────────
# What:  Proves that veydus_app cannot UPDATE or DELETE from the
#        audit_log table.
# How:   Connects as veydus_app, inserts an audit row (allowed),
#        then attempts UPDATE and DELETE (both must be denied).
# Why:   The audit log is append-only by design.  If the runtime
#        role could modify or delete audit rows, an attacker who
#        compromises the application could erase evidence of their
#        actions.  The GRANT is SELECT + INSERT only (HLD §5.4).
# Ref:   Implementation plan A1 success check 6.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import json

import psycopg2
import pytest


@pytest.mark.isolation
class TestAuditImmutability:
    """audit_log must be append-only for veydus_app."""

    def _insert_test_audit_row(self, conn, org_id: str) -> int:
        """Insert a test audit row and return its id.

        Requires SET LOCAL veydus.org_id because audit_log has RLS.
        """
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("SET LOCAL veydus.org_id = %s", (org_id,))
            cur.execute(
                """
                INSERT INTO audit_log (org_id, event_type, payload)
                VALUES (%s, 'test_event', %s)
                RETURNING id
                """,
                (org_id, json.dumps({"test": True})),
            )
            row_id = cur.fetchone()[0]
            cur.execute("COMMIT")
        return row_id

    def test_update_audit_log_denied(self, app_conn, seeded_orgs):
        """UPDATE on audit_log must raise a permission error.

        Even with a valid org context, the application role cannot
        modify existing audit rows.
        """
        org_id = str(seeded_orgs["org_a"]["id"])
        row_id = self._insert_test_audit_row(app_conn, org_id)

        with pytest.raises(psycopg2.errors.InsufficientPrivilege), app_conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("SET LOCAL veydus.org_id = %s", (org_id,))
            cur.execute(
                "UPDATE audit_log SET event_type = 'tampered' WHERE id = %s",
                (row_id,),
            )

        # Roll back the failed transaction so the connection is usable
        app_conn.rollback()

    def test_delete_audit_log_denied(self, app_conn, seeded_orgs):
        """DELETE on audit_log must raise a permission error.

        The runtime role cannot erase audit evidence.
        """
        org_id = str(seeded_orgs["org_a"]["id"])
        row_id = self._insert_test_audit_row(app_conn, org_id)

        with pytest.raises(psycopg2.errors.InsufficientPrivilege), app_conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("SET LOCAL veydus.org_id = %s", (org_id,))
            cur.execute(
                "DELETE FROM audit_log WHERE id = %s",
                (row_id,),
            )

        app_conn.rollback()

    def test_insert_audit_log_allowed(self, app_conn, seeded_orgs):
        """INSERT on audit_log must succeed (append-only, not no-access).

        This is the positive control: the role CAN write audit rows.
        """
        org_id = str(seeded_orgs["org_a"]["id"])
        row_id = self._insert_test_audit_row(app_conn, org_id)
        assert row_id > 0, "INSERT into audit_log should succeed"

    def test_select_audit_log_allowed(self, app_conn, seeded_orgs):
        """SELECT on audit_log must succeed (operators need to read it)."""
        org_id = str(seeded_orgs["org_a"]["id"])
        self._insert_test_audit_row(app_conn, org_id)

        with app_conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("SET LOCAL veydus.org_id = %s", (org_id,))
            cur.execute("SELECT count(*) FROM audit_log")
            count = cur.fetchone()[0]
            cur.execute("COMMIT")

        assert count > 0, "SELECT from audit_log should return rows"
