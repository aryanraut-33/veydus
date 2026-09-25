# ─────────────────────────────────────────────────────────────────
# VEYDUS — Connection Pooling Hazard Test  (HLD §5.4, §17.2)
# ─────────────────────────────────────────────────────────────────
# What:  Proves that 200 interleaved requests alternating between
#        Org A and Org B over a pool of 5 connections produce zero
#        cross-tenant rows, and that no connection retains a
#        residual veydus.org_id after all requests complete.
# How:   Creates a psycopg2 connection pool (5 connections), then
#        runs 200 iterations.  Each iteration picks an org, gets a
#        connection, BEGINs, SET LOCALs, queries chunks, COMMITs,
#        and returns the connection.  Every result is checked.
# Why:   SET (without LOCAL) persists on a pooled connection.  If
#        the session.py context manager is ever changed to use SET
#        instead of SET LOCAL, this test catches the leak.
# Ref:   Implementation plan A1 success check 4.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import pytest
from psycopg2 import pool

from tests.conftest import APP_PASSWORD, APP_USER


@pytest.mark.isolation
class TestPoolingHazard:
    """Connection pool must never leak tenant context across requests."""

    def test_200_interleaved_requests_zero_cross_tenant_rows(self, migrated_db, seeded_orgs):
        """200 requests, alternating orgs, shared pool → zero leaks.

        Each request:
          1. Get a connection from the pool
          2. BEGIN
          3. SET LOCAL veydus.org_id = <org>
          4. SELECT id, org_id FROM chunks
          5. Assert every returned row belongs to the expected org
          6. COMMIT (SET LOCAL is discarded)
          7. Return connection to pool

        After all 200: check every connection for residual org_id.
        """
        # Create a pool of 5 connections as veydus_app
        conn_pool = pool.SimpleConnectionPool(
            minconn=5,
            maxconn=5,
            host=migrated_db["host"],
            port=migrated_db["port"],
            dbname=migrated_db["dbname"],
            user=APP_USER,
            password=APP_PASSWORD,
        )

        org_a_id = str(seeded_orgs["org_a"]["id"])
        org_b_id = str(seeded_orgs["org_b"]["id"])
        orgs = [org_a_id, org_b_id]

        cross_tenant_violations = []

        try:
            for i in range(200):
                # Alternate between Org A and Org B
                expected_org_id = orgs[i % 2]

                conn = conn_pool.getconn()
                try:
                    with conn.cursor() as cur:
                        cur.execute("BEGIN")
                        cur.execute("SET LOCAL veydus.org_id = %s", (expected_org_id,))
                        cur.execute("SELECT id, org_id FROM chunks")
                        rows = cur.fetchall()
                        cur.execute("COMMIT")

                    # Every row must belong to the expected org
                    for row_id, row_org_id in rows:
                        if str(row_org_id) != expected_org_id:
                            cross_tenant_violations.append(
                                {
                                    "iteration": i,
                                    "expected_org": expected_org_id,
                                    "got_org": str(row_org_id),
                                    "chunk_id": str(row_id),
                                }
                            )
                finally:
                    conn_pool.putconn(conn)

            assert len(cross_tenant_violations) == 0, (
                f"CROSS-TENANT LEAK: {len(cross_tenant_violations)} rows from "
                f"wrong org across 200 interleaved requests.\n"
                f"First violation: {cross_tenant_violations[0]}"
            )

        finally:
            conn_pool.closeall()

    def test_no_residual_org_id_after_transaction(self, migrated_db, seeded_orgs):
        """After COMMIT, the connection must carry no veydus.org_id.

        If SET LOCAL works correctly, the setting is discarded on
        COMMIT.  current_setting with missing_ok=true returns NULL.
        """
        conn_pool = pool.SimpleConnectionPool(
            minconn=5,
            maxconn=5,
            host=migrated_db["host"],
            port=migrated_db["port"],
            dbname=migrated_db["dbname"],
            user=APP_USER,
            password=APP_PASSWORD,
        )

        org_a_id = str(seeded_orgs["org_a"]["id"])
        residual_leaks = []

        try:
            # Use each connection once with SET LOCAL, then check
            for i in range(5):
                conn = conn_pool.getconn()
                try:
                    with conn.cursor() as cur:
                        # Use SET LOCAL inside a transaction
                        cur.execute("BEGIN")
                        cur.execute("SET LOCAL veydus.org_id = %s", (org_a_id,))
                        cur.execute("COMMIT")

                        # After COMMIT, the setting must be gone (evaluates to NULL)
                        cur.execute("SELECT nullif(current_setting('veydus.org_id', true), '')")
                        row = cur.fetchone()
                        residual = row[0] if row else None

                        if residual is not None:
                            residual_leaks.append({"connection": i, "residual_org_id": residual})
                finally:
                    conn_pool.putconn(conn)

            assert len(residual_leaks) == 0, (
                f"POOLING HAZARD: {len(residual_leaks)} connections retained "
                f"veydus.org_id after COMMIT.\n"
                f"This means SET LOCAL is not working — likely using SET instead.\n"
                f"Leaks: {residual_leaks}"
            )

        finally:
            conn_pool.closeall()
