# ─────────────────────────────────────────────────────────────────
# VEYDUS — pgvector Version Assertion  (HLD §6.4)
# ─────────────────────────────────────────────────────────────────
# What:  Asserts the pgvector extension version is >= 0.8.0.
# How:   Queries pg_extension for the installed vector version and
#        compares against the minimum required version.
# Why:   pgvector >= 0.8.0 provides iterative index scans, which
#        are essential for maintaining recall under restrictive
#        access filters.  Without iterative scans, a level-1 user
#        in a small department may get sparse or empty results
#        because HNSW filtered out most candidates during traversal.
# Ref:   Implementation plan A1 success check 2.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import pytest


@pytest.mark.integration
class TestPgvectorVersion:
    """pgvector must be >= 0.8.0 for iterative index scan support."""

    def test_pgvector_installed(self, superuser_conn, migrated_db):
        """The vector extension must be installed."""
        with superuser_conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            result = cur.fetchone()

        assert result is not None, (
            "pgvector extension is not installed. "
            "Run CREATE EXTENSION vector or use the pgvector/pgvector Docker image."
        )

    def test_pgvector_version_minimum(self, superuser_conn, migrated_db):
        """pgvector version must be >= 0.8.0.

        Versions below 0.8.0 lack iterative index scans, breaking
        filtered ANN recall for restrictive access predicates (HLD §6.4).
        """
        with superuser_conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            result = cur.fetchone()

        assert result is not None, "pgvector not installed"
        version_str = result[0]

        # Parse version: "0.8.0" → (0, 8, 0)
        parts = tuple(int(p) for p in version_str.split("."))
        minimum = (0, 8, 0)

        assert parts >= minimum, (
            f"pgvector version {version_str} is below the minimum 0.8.0. "
            f"Iterative index scans (HLD §6.4) require >= 0.8.0. "
            f"Update the pgvector/pgvector Docker image."
        )

    def test_pgcrypto_installed(self, superuser_conn, migrated_db):
        """pgcrypto extension must be installed (HLD §5.2)."""
        with superuser_conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'pgcrypto'")
            result = cur.fetchone()

        assert result is not None, "pgcrypto extension is not installed."
