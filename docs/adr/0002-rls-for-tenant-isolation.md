# ADR-0002: Row-Level Security for Tenant Isolation

| Field    | Value                                  |
|----------|----------------------------------------|
| Status   | Accepted                               |
| Date     | 2026-09-25                             |
| Decides  | Use PostgreSQL RLS as the primary tenant isolation mechanism |

## Context

VEYDUS is multi-tenant. Every query must return data belonging only to the requesting organization. The conventional approach is application-level `WHERE org_id = ?` on every query.

## Decision

Use **PostgreSQL Row-Level Security (RLS)** with `ENABLE` + `FORCE` on every tenant-scoped table, enforced via `SET LOCAL veydus.org_id` inside a transaction-scoped context manager.

## Rationale

1. **Defence in depth.** A missing `WHERE org_id = ?` in application code is a silent cross-tenant leak. A missing `SET LOCAL` returns zero rows — a loud failure, not a quiet one.
2. **Impossible to bypass from application code.** Even if a repository function forgets to filter by org_id, RLS blocks the wrong rows at the database level.
3. **SET LOCAL is transaction-scoped.** It is automatically discarded on COMMIT or ROLLBACK, preventing the connection-pooling hazard where a stale org_id leaks to the next request.
4. **FORCE ROW LEVEL SECURITY** makes even the table owner subject to RLS (belt and suspenders — the app should never run as the owner, but if it did, FORCE prevents a bypass).

## Consequences

- Every database-touching code path must go through `tenant_transaction()`. No raw connections allowed.
- The `veydus_app` role must NOT be the table owner and must NOT have `BYPASSRLS`.
- A CI check must verify RLS policy coverage on every table.
