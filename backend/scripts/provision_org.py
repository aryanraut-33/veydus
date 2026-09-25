#!/usr/bin/env python3
"""
VEYDUS — Organization Provisioning Script  (Implementation Plan §4 Q2)
─────────────────────────────────────────────────────────────────────
What:  CLI tool to create a new organization, its org_settings row,
       and its first operator user.  This is the ONLY way to create
       organizations — there is no platform admin API or UI.

How:   Connects directly to PostgreSQL as veydus_migrate (BYPASSRLS)
       using psycopg2 (sync driver).  Inserts into organizations,
       org_settings, and users in a single transaction.

Why:   PRD §11 forbids self-service organization provisioning.
       PRD §15.1 says organizations are provisioned manually by the
       Platform Administrator.  This script is that mechanism.

Usage:
    python scripts/provision_org.py \\
        --name "Acme Corp" \\
        --slug "acme" \\
        --operator-email "admin@acme.com" \\
        --operator-name "Alice Admin"

    # With a custom database URL:
    python scripts/provision_org.py \\
        --name "Acme Corp" \\
        --slug "acme" \\
        --operator-email "admin@acme.com" \\
        --db-url "postgresql://veydus_migrate:pw@host:5432/veydus"

Note:  The idp_subject is auto-generated as a UUID placeholder.
       When Identity Platform is set up (Sprint A5), real subjects
       will be used.  For now, this value is a unique identifier
       that satisfies the NOT NULL UNIQUE constraint.
"""

from __future__ import annotations

import argparse
import sys
import uuid

import psycopg2

# Default connection for local development
_DEFAULT_DB_URL = "postgresql://veydus_migrate:veydus_migrate_pw@localhost:5432/veydus"


def provision_organization(
    db_url: str,
    name: str,
    slug: str,
    operator_email: str,
    operator_name: str | None = None,
) -> dict[str, str]:
    """Create an organization with its settings and first operator.

    All three inserts happen in a single transaction.  If any fails,
    the entire operation is rolled back — no partial organizations.

    Args:
        db_url:          PostgreSQL connection string (veydus_migrate role).
        name:            Human-readable organization name.
        slug:            URL-safe unique slug (e.g. "acme").
        operator_email:  Email address for the first operator user.
        operator_name:   Optional display name for the operator.

    Returns:
        Dict with 'org_id', 'user_id', and 'idp_subject' of the
        created records.
    """
    # Generate a placeholder IDP subject — replaced by real Identity
    # Platform subjects once auth is set up in Sprint A5.
    idp_subject = f"placeholder-{uuid.uuid4()}"

    conn = psycopg2.connect(db_url)
    try:
        with conn, conn.cursor() as cur:
            # 1. Create the organization
            cur.execute(
                """
                    INSERT INTO organizations (name, slug)
                    VALUES (%s, %s)
                    RETURNING id
                    """,
                (name, slug),
            )
            org_id = cur.fetchone()[0]  # type: ignore[index]

            # 2. Create default org_settings (all defaults from HLD §5.2)
            cur.execute(
                "INSERT INTO org_settings (org_id) VALUES (%s)",
                (org_id,),
            )

            # 3. Create the first operator user
            cur.execute(
                """
                    INSERT INTO users (org_id, idp_subject, email, display_name, role, status)
                    VALUES (%s, %s, %s, %s, 'operator', 'active')
                    RETURNING id
                    """,
                (org_id, idp_subject, operator_email, operator_name),
            )
            user_id = cur.fetchone()[0]  # type: ignore[index]

        return {
            "org_id": str(org_id),
            "user_id": str(user_id),
            "idp_subject": idp_subject,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Provision a new VEYDUS organization with its first operator.",
    )
    parser.add_argument("--name", required=True, help="Organization display name")
    parser.add_argument("--slug", required=True, help="URL-safe unique slug")
    parser.add_argument("--operator-email", required=True, help="First operator's email")
    parser.add_argument("--operator-name", default=None, help="First operator's display name")
    parser.add_argument(
        "--db-url",
        default=_DEFAULT_DB_URL,
        help=f"PostgreSQL URL (default: {_DEFAULT_DB_URL})",
    )

    args = parser.parse_args()

    try:
        result = provision_organization(
            db_url=args.db_url,
            name=args.name,
            slug=args.slug,
            operator_email=args.operator_email,
            operator_name=args.operator_name,
        )
    except psycopg2.errors.UniqueViolation as e:
        print(f"ERROR: Organization or user already exists — {e}", file=sys.stderr)
        sys.exit(1)
    except psycopg2.OperationalError as e:
        print(f"ERROR: Cannot connect to database — {e}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ Organization created:  {result['org_id']}")
    print(f"  Operator user:         {result['user_id']}")
    print(f"  IDP subject:           {result['idp_subject']}")
    print(f"  Slug:                  {args.slug}")


if __name__ == "__main__":
    main()
