-- ─────────────────────────────────────────────────────────────────
-- VEYDUS — Database Role Initialization  (HLD §5.4)
-- ─────────────────────────────────────────────────────────────────
-- What:  Creates the two PostgreSQL roles required by the system.
-- When:  Executed automatically by the Postgres container on first
--        start (mounted into /docker-entrypoint-initdb.d/).
-- Who:   Runs as the 'postgres' superuser.
--
-- Roles defined (per HLD §5.4):
--
--   veydus_migrate  — Table owner.  Runs Alembic migrations only.
--                     Has BYPASSRLS so it can seed data regardless
--                     of row-level security policies.  Never used
--                     at application runtime.
--
--   veydus_app      — Runtime role for veydus-api and veydus-worker.
--                     NOT a table owner.  Does NOT have BYPASSRLS.
--                     Subject to ALL RLS policies.
--
-- The migration (Alembic) grants table-level permissions to
-- veydus_app after creating the schema.
-- ─────────────────────────────────────────────────────────────────

-- Extensions: required by HLD §5.2; must be created by superuser
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Migration role: owns tables, bypasses RLS for admin/seeding.
CREATE ROLE veydus_migrate WITH LOGIN PASSWORD 'veydus_migrate_pw' BYPASSRLS;

-- Application role: runtime access, fully subject to RLS.
-- Deliberately NOT given BYPASSRLS or table ownership.
CREATE ROLE veydus_app WITH LOGIN PASSWORD 'veydus_app_pw';

-- Database-level permissions
GRANT ALL PRIVILEGES ON DATABASE veydus TO veydus_migrate;
GRANT CONNECT ON DATABASE veydus TO veydus_app;

-- Schema-level permissions (table-level grants are in the migration)
GRANT ALL PRIVILEGES ON SCHEMA public TO veydus_migrate;
GRANT USAGE ON SCHEMA public TO veydus_app;
