<!-- 
  Architecture Decision Record: ABAC with ReBAC Migration Path
  What: Documents the architectural decision to implement Attribute-Based Access Control (ABAC)
        for MVP while preserving a clean migration pathway toward Relationship-Based Access Control (ReBAC).
  Standards: Follows MADR template and HLD §5.1, §6.3, §16.
-->

# ADR-0003: Attribute-Based Access Control (ABAC) with ReBAC Migration Path

| Field    | Value                                                              |
|----------|--------------------------------------------------------------------|
| Status   | Accepted                                                           |
| Date     | 2026-09-25                                                         |
| Decides  | Implement ABAC for MVP with architectural isolation for ReBAC      |

## Context

VEYDUS delivers enterprise RAG with hierarchical clearance levels and department-based boundaries. Enterprise access control generally follows either:
1. **Attribute-Based Access Control (ABAC)**: Access evaluation depends on subject and resource attributes (e.g., `user.department_id == chunk.department_id AND user.hierarchy_level >= chunk.hierarchy_level`).
2. **Relationship-Based Access Control (ReBAC)**: Access evaluation depends on a graph of relationships (e.g., Google Zanzibar / SpiceDB / OpenFGA).

While full ReBAC supports complex arbitrary resource sharing and transitive team hierarchies, it introduces substantial operational complexity, separate service dependencies, and non-trivial query latency during filtered ANN vector scans.

## Decision

Implement **Attribute-Based Access Control (ABAC)** directly in PostgreSQL for the MVP using `department_id` and `hierarchy_level` attributes, while strictly enforcing that **all access predicates are generated through a single chokepoint** (`authz/policy.py`).

## Rationale

1. **PostgreSQL Pushdown & Latency**: Evaluating ABAC predicates directly within the single retrieval SQL query allows PostgreSQL's query planner to leverage composite B-tree and HNSW iterative index scans without crossing service boundaries.
2. **Deterministic MVP Guarantees**: Department containment and clearance hierarchy satisfy 100% of the PRD requirements for Phase A and Phase B.
3. **Isolated Migration Boundary**: Application code and repository queries never construct raw access checks; they exclusively call `authz/policy.py`. When enterprise requirements necessitate ReBAC (e.g., cross-department ad-hoc collaboration graphs), the evaluation logic in `authz/policy.py` can be refactored to resolve to tuple sets without rewriting downstream database schemas or vector search queries.

## Consequences

- Direct department and level checks are prohibited outside `authz/policy.py` and `db/repositories/chunks.py`.
- Schema columns (`department_id`, `hierarchy_level`) are explicitly indexed on `chunks` alongside `org_id`.
- ReBAC remains an evolutionary step post-MVP rather than a day-one dependency.
