"""Sprint A1: Initial schema, RLS, roles, and indexes.

Creates the complete HLD §5.2 schema in one revision:
  - 14 tables (organizations through audit_log)
  - pgvector and pgcrypto extensions
  - Row-Level Security (ENABLE + FORCE) on every tenant-scoped table
  - tenant_isolation policy on each table
  - All HLD §5.3 indexes EXCEPT idx_chunks_embedding (built after
    bulk ingestion per HLD §5.3)
  - Table-level GRANT to veydus_app (HLD §5.4)
  - Append-only audit_log grants (SELECT + INSERT only)

Runs as veydus_migrate (table owner, BYPASSRLS).

Revision ID: a1_0001
Revises: None
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─────────────────────────────────────────────────────────────────
# Every tenant-scoped table.  Used to apply RLS and policies
# uniformly.  The organizations table uses 'id' instead of
# 'org_id' because it IS the tenancy root.
# ─────────────────────────────────────────────────────────────────
_TENANT_TABLES_WITH_ORG_ID = [
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


def upgrade() -> None:
    # ─────────────────────────────────────────────────────────────
    # 1. Extensions
    # ─────────────────────────────────────────────────────────────
    # pgvector: vector similarity search (HLD §5.2)
    # pgcrypto: gen_random_uuid() and cryptographic functions
    # Note: Extensions are pre-installed by superuser in init-db.sql and test conftest.
    bind = op.get_bind()
    has_vector = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    if not has_vector:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    has_crypto = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'")
    ).scalar()
    if not has_crypto:
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ─────────────────────────────────────────────────────────────
    # 2. Tables — exact HLD §5.2 definitions
    # ─────────────────────────────────────────────────────────────

    # -- Tenancy root --
    op.execute("""
        CREATE TABLE organizations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name            TEXT NOT NULL,
            slug            TEXT NOT NULL UNIQUE,
            status          TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'suspended')),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -- Departments and hierarchy --
    op.execute("""
        CREATE TABLE departments (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            max_level       INT  NOT NULL CHECK (max_level >= 1),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (org_id, name)
        )
    """)

    # -- Identity and access --
    op.execute("""
        CREATE TABLE users (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            idp_subject         TEXT NOT NULL UNIQUE,
            email               TEXT NOT NULL,
            display_name        TEXT,
            role                TEXT NOT NULL DEFAULT 'user'
                                CHECK (role IN ('user', 'operator')),
            status              TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'active', 'revoked')),
            scope_version       INT  NOT NULL DEFAULT 1,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (org_id, email)
        )
    """)

    # [SECURITY-CRITICAL] One row per user — structurally prevents
    # accumulating multiple grants where the query picks the most
    # permissive (HLD §5.2).
    op.execute("""
        CREATE TABLE access_grants (
            user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            department_id       UUID NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
            hierarchy_level     INT  NOT NULL CHECK (hierarchy_level >= 1),
            granted_by          UUID NOT NULL REFERENCES users(id),
            granted_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE access_requests (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            requested_note      TEXT,
            status              TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'approved', 'denied')),
            decided_by          UUID REFERENCES users(id),
            decided_at          TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -- Knowledge sources --
    op.execute("""
        CREATE TABLE sources (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            kind                TEXT NOT NULL CHECK (kind IN ('upload', 'gdrive')),
            display_name        TEXT NOT NULL,
            department_id       UUID NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
            hierarchy_level     INT  NOT NULL CHECK (hierarchy_level >= 1),
            config              JSONB NOT NULL DEFAULT '{}'::jsonb,
            status              TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'processing', 'ready', 'failed', 'deleting')),
            error_detail        TEXT,
            created_by          UUID NOT NULL REFERENCES users(id),
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE documents (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            source_id           UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            external_ref        TEXT,
            title               TEXT NOT NULL,
            mime_type           TEXT NOT NULL,
            page_count          INT,
            content_hash        TEXT NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Chunks: denormalised department_id + hierarchy_level so the
    # access predicate applies to the same table as the vector index,
    # with no join (HLD §5.2 denormalisation rationale).
    op.execute("""
        CREATE TABLE chunks (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            document_id         UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            source_id           UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            department_id       UUID NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
            hierarchy_level     INT  NOT NULL CHECK (hierarchy_level >= 1),
            ordinal             INT  NOT NULL,
            content             TEXT NOT NULL,
            token_count         INT  NOT NULL,
            page_number         INT,
            embedding           vector(768) NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -- Conversations and session memory --
    op.execute("""
        CREATE TABLE conversations (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id                  UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title                   TEXT,
            scope_version           INT  NOT NULL,
            status                  TEXT NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'invalidated')),
            rolling_summary         TEXT,
            summary_upto_ordinal    INT NOT NULL DEFAULT 0,
            summary_max_level       INT,
            summary_department_id   UUID REFERENCES departments(id),
            created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE messages (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            ordinal             INT  NOT NULL,
            role                TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content             TEXT NOT NULL,
            citations           JSONB NOT NULL DEFAULT '[]'::jsonb,
            refused             BOOLEAN NOT NULL DEFAULT false,
            token_count         INT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (conversation_id, ordinal)
        )
    """)

    # Session chunks: separate from chunks on purpose — session
    # uploads have no department/level and are scoped to a single
    # conversation + user (HLD §5.2).
    op.execute("""
        CREATE TABLE session_chunks (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            conversation_id     UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            filename            TEXT NOT NULL,
            ordinal             INT  NOT NULL,
            content             TEXT NOT NULL,
            embedding           vector(768) NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -- Configuration --
    op.execute("""
        CREATE TABLE org_settings (
            org_id                  UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
            generation_model        TEXT NOT NULL DEFAULT 'meta/llama-3.3-70b-instruct',
            grounding_threshold     REAL NOT NULL DEFAULT 0.35
                                    CHECK (grounding_threshold BETWEEN 0 AND 1),
            temperature             REAL NOT NULL DEFAULT 0.2
                                    CHECK (temperature BETWEEN 0 AND 2),
            max_response_tokens     INT  NOT NULL DEFAULT 1024,
            refusal_message         TEXT NOT NULL DEFAULT
                'No information available, or it exists above your access level.',
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -- Operations --
    op.execute("""
        CREATE TABLE ingestion_jobs (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            source_id           UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            job_type            TEXT NOT NULL CHECK (job_type IN ('ingest', 'delete')),
            status              TEXT NOT NULL DEFAULT 'queued'
                                CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
            attempt             INT  NOT NULL DEFAULT 0,
            documents_total     INT,
            documents_done      INT NOT NULL DEFAULT 0,
            error_detail        TEXT,
            started_at          TIMESTAMPTZ,
            finished_at         TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Audit log: append-only.  No UPDATE or DELETE grant (HLD §5.4).
    op.execute("""
        CREATE TABLE audit_log (
            id                  BIGSERIAL PRIMARY KEY,
            org_id              UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            actor_user_id       UUID REFERENCES users(id),
            event_type          TEXT NOT NULL,
            payload             JSONB NOT NULL,
            trace_id            TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ─────────────────────────────────────────────────────────────
    # 3. Row-Level Security  [SECURITY-CRITICAL]  (HLD §5.4)
    # ─────────────────────────────────────────────────────────────
    # ENABLE + FORCE on every table.
    # FORCE means even the table owner is subject to RLS (defence
    # in depth — the app should never connect as the owner, but if
    # it did, FORCE prevents a silent bypass).

    # organizations: uses 'id' as the tenant column (it IS the org)
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE  ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation ON organizations
            USING      (id = nullif(current_setting('veydus.org_id', true), '')::uuid)
            WITH CHECK (id = nullif(current_setting('veydus.org_id', true), '')::uuid)
    """)

    # All other tenant-scoped tables: use 'org_id'
    for table in _TENANT_TABLES_WITH_ORG_ID:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE  ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
                USING      (org_id = nullif(current_setting('veydus.org_id', true), '')::uuid)
                WITH CHECK (org_id = nullif(current_setting('veydus.org_id', true), '')::uuid)
        """)

    # ─────────────────────────────────────────────────────────────
    # 4. Indexes  (HLD §5.3)
    # ─────────────────────────────────────────────────────────────
    # NOTE: idx_chunks_embedding is deliberately NOT created here.
    # It is built AFTER bulk ingestion (Sprint A6) per HLD §5.3,
    # because building HNSW on an empty or tiny table wastes time
    # and produces a suboptimal graph.

    # Access filter support — composite, ordered to match the predicate
    op.execute("""
        CREATE INDEX idx_chunks_access
            ON chunks (org_id, department_id, hierarchy_level)
    """)
    op.execute("CREATE INDEX idx_chunks_source   ON chunks (source_id)")
    op.execute("CREATE INDEX idx_chunks_document ON chunks (document_id)")

    # Session chunks
    op.execute("CREATE INDEX idx_session_chunks_conv ON session_chunks (conversation_id)")
    op.execute("""
        CREATE INDEX idx_session_chunks_embedding
            ON session_chunks USING hnsw (embedding vector_cosine_ops)
    """)

    # Conversations, messages, audit, users
    op.execute("CREATE INDEX idx_messages_conv      ON messages (conversation_id, ordinal)")
    op.execute("CREATE INDEX idx_conversations_user ON conversations (user_id, updated_at DESC)")
    op.execute("CREATE INDEX idx_audit_org_time     ON audit_log (org_id, created_at DESC)")
    op.execute("CREATE INDEX idx_users_idp          ON users (idp_subject)")

    # ─────────────────────────────────────────────────────────────
    # 5. Table-level grants to veydus_app  (HLD §5.4)
    # ─────────────────────────────────────────────────────────────
    # Full CRUD on all tables except audit_log
    op.execute("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            organizations, departments, users, access_grants,
            access_requests, sources, documents, chunks,
            conversations, messages, session_chunks, org_settings,
            ingestion_jobs
        TO veydus_app
    """)

    # audit_log: append-only — SELECT + INSERT only.
    # Explicit REVOKE for belt-and-suspenders even though we never
    # granted UPDATE/DELETE.
    op.execute("GRANT SELECT, INSERT ON audit_log TO veydus_app")
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM veydus_app")

    # Sequences (BIGSERIAL PK on audit_log needs USAGE)
    op.execute("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO veydus_app")

    # Default privileges so future migrations automatically grant
    # to veydus_app without remembering to add explicit GRANTs.
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE veydus_migrate IN SCHEMA public
            GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO veydus_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE veydus_migrate IN SCHEMA public
            GRANT USAGE ON SEQUENCES TO veydus_app
    """)


def downgrade() -> None:
    """Drop everything in reverse order.

    Used in development only.  Production migrations are forward-only.
    """
    # Drop tables in reverse dependency order
    tables = [
        "audit_log",
        "ingestion_jobs",
        "org_settings",
        "session_chunks",
        "messages",
        "conversations",
        "chunks",
        "documents",
        "sources",
        "access_requests",
        "access_grants",
        "users",
        "departments",
        "organizations",
    ]
    for table in tables:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    # Revoke default privileges
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE veydus_migrate IN SCHEMA public
            REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM veydus_app
    """)
    op.execute("""
        ALTER DEFAULT PRIVILEGES FOR ROLE veydus_migrate IN SCHEMA public
            REVOKE USAGE ON SEQUENCES FROM veydus_app
    """)
