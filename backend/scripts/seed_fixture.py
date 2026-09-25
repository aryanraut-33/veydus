#!/usr/bin/env python3
"""
VEYDUS — Seed Fixture Script  (Sprint A1)
─────────────────────────────────────────────────────────────────────
What:  Seeds TWO organizations with departments, users, and access
       grants for development and testing.

How:   Connects as veydus_migrate (BYPASSRLS) using psycopg2.
       Inserts all data in a single transaction — either everything
       succeeds or nothing does.

Why:   The implementation plan requires two organizations from day
       one so that cross-tenant and pooling hazard tests operate on
       real data, not synthetic single-org scenarios.

Fixture structure:
    Organization A — "Acme Corp" (slug: acme)
        ├── Department: Finance    (max_level=3)
        ├── Department: Engineering (max_level=3)
        ├── User: operator@acme.com  (operator, active)
        └── User: analyst@acme.com   (user, active, Finance L2)

    Organization B — "Globex Inc" (slug: globex)
        ├── Department: HR         (max_level=3)
        ├── Department: Sales      (max_level=3)
        ├── User: operator@globex.com (operator, active)
        └── User: rep@globex.com      (user, active, HR L1)

Usage:
    python scripts/seed_fixture.py
    python scripts/seed_fixture.py --db-url "postgresql://..."
"""

from __future__ import annotations

import argparse
import sys
import uuid

import psycopg2

_DEFAULT_DB_URL = "postgresql://veydus_migrate:veydus_migrate_pw@localhost:5432/veydus"


def _make_idp_subject(label: str) -> str:
    """Generate a deterministic-ish placeholder IDP subject.

    Uses a fixed namespace UUID so re-running produces the same
    subjects, making the fixture idempotent (on conflict, the
    UNIQUE constraint fires).
    """
    namespace = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    return str(uuid.uuid5(namespace, label))


def seed_fixture(db_url: str) -> None:
    """Insert the complete two-org fixture into the database.

    Runs in one transaction so partial seeding is impossible.
    Uses veydus_migrate (BYPASSRLS) — no SET LOCAL needed.
    """
    conn = psycopg2.connect(db_url)
    try:
        with conn, conn.cursor() as cur:
            # ─── Organization A: Acme Corp ───────────────────
            cur.execute(
                """
                    INSERT INTO organizations (name, slug)
                    VALUES ('Acme Corp', 'acme')
                    RETURNING id
                    """,
            )
            org_a = cur.fetchone()[0]  # type: ignore[index]

            cur.execute(
                "INSERT INTO org_settings (org_id) VALUES (%s)",
                (org_a,),
            )

            # Departments
            cur.execute(
                """
                    INSERT INTO departments (org_id, name, max_level)
                    VALUES (%s, 'Finance', 3)
                    RETURNING id
                    """,
                (org_a,),
            )
            dept_finance = cur.fetchone()[0]  # type: ignore[index]

            cur.execute(
                """
                    INSERT INTO departments (org_id, name, max_level)
                    VALUES (%s, 'Engineering', 3)
                    RETURNING id
                    """,
                (org_a,),
            )
            dept_engineering = cur.fetchone()[0]  # type: ignore[index]

            # Operator (no access_grant — operators access via role, not dept/level)
            cur.execute(
                """
                    INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                    VALUES (%s, %s, 'operator@acme.com', 'Acme Admin', 'operator', 'active')
                    RETURNING id
                    """,
                (org_a, _make_idp_subject("operator@acme.com")),
            )
            operator_a = cur.fetchone()[0]  # type: ignore[index]

            # Regular user with Finance L2 access
            cur.execute(
                """
                    INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                    VALUES (%s, %s, 'analyst@acme.com', 'Acme Analyst', 'user', 'active')
                    RETURNING id
                    """,
                (org_a, _make_idp_subject("analyst@acme.com")),
            )
            user_a = cur.fetchone()[0]  # type: ignore[index]

            cur.execute(
                """
                    INSERT INTO access_grants (user_id, org_id, department_id, hierarchy_level, granted_by)
                    VALUES (%s, %s, %s, 2, %s)
                    """,
                (user_a, org_a, dept_finance, operator_a),
            )

            # ─── Organization B: Globex Inc ──────────────────
            cur.execute(
                """
                    INSERT INTO organizations (name, slug)
                    VALUES ('Globex Inc', 'globex')
                    RETURNING id
                    """,
            )
            org_b = cur.fetchone()[0]  # type: ignore[index]

            cur.execute(
                "INSERT INTO org_settings (org_id) VALUES (%s)",
                (org_b,),
            )

            # Departments
            cur.execute(
                """
                    INSERT INTO departments (org_id, name, max_level)
                    VALUES (%s, 'HR', 3)
                    RETURNING id
                    """,
                (org_b,),
            )
            dept_hr = cur.fetchone()[0]  # type: ignore[index]

            cur.execute(
                """
                    INSERT INTO departments (org_id, name, max_level)
                    VALUES (%s, 'Sales', 3)
                    RETURNING id
                    """,
                (org_b,),
            )
            _dept_sales = cur.fetchone()[0]  # type: ignore[index]  # noqa: F841

            # Operator
            cur.execute(
                """
                    INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                    VALUES (%s, %s, 'operator@globex.com', 'Globex Admin', 'operator', 'active')
                    RETURNING id
                    """,
                (org_b, _make_idp_subject("operator@globex.com")),
            )
            operator_b = cur.fetchone()[0]  # type: ignore[index]

            # Regular user with HR L1 access
            cur.execute(
                """
                    INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                    VALUES (%s, %s, 'rep@globex.com', 'Globex Rep', 'user', 'active')
                    RETURNING id
                    """,
                (org_b, _make_idp_subject("rep@globex.com")),
            )
            user_b = cur.fetchone()[0]  # type: ignore[index]

            cur.execute(
                """
                    INSERT INTO access_grants (user_id, org_id, department_id, hierarchy_level, granted_by)
                    VALUES (%s, %s, %s, 1, %s)
                    """,
                (user_b, org_b, dept_hr, operator_b),
            )

        # Print summary (outside transaction — data is committed)
        print("✓ Fixture seeded successfully:")
        print(f"  Org A (Acme Corp):  {org_a}")
        print(f"    Finance:          {dept_finance}")
        print(f"    Engineering:      {dept_engineering}")
        print(f"    Operator:         {operator_a}")
        print(f"    User (Fin L2):    {user_a}")
        print(f"  Org B (Globex Inc): {org_b}")
        print(f"    HR:               {dept_hr}")
        print(f"    Sales:            {_dept_sales}")
        print(f"    Operator:         {operator_b}")
        print(f"    User (HR L1):     {user_b}")

    except psycopg2.errors.UniqueViolation:
        print("⚠ Fixture already exists (unique constraint hit). Skipping.", file=sys.stderr)
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the VEYDUS database with two test organizations.",
    )
    parser.add_argument(
        "--db-url",
        default=_DEFAULT_DB_URL,
        help=f"PostgreSQL URL (default: {_DEFAULT_DB_URL})",
    )
    args = parser.parse_args()

    try:
        seed_fixture(args.db_url)
    except psycopg2.OperationalError as e:
        print(f"ERROR: Cannot connect to database — {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
