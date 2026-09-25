# ADR-0001: pgvector Over a Dedicated Vector Database

| Field    | Value                                  |
|----------|----------------------------------------|
| Status   | Accepted                               |
| Date     | 2026-09-25                             |
| Decides  | Use pgvector (PostgreSQL extension) instead of a dedicated vector database |

## Context

VEYDUS requires vector similarity search for retrieval-augmented generation. The access filter (`department_id`, `hierarchy_level`) must be applied **during** the ANN scan, not after, to prevent the planner from post-filtering results (which degrades recall under restrictive filters).

## Decision

Use **pgvector** as a PostgreSQL extension rather than a standalone vector database (Pinecone, Weaviate, Qdrant, etc.).

## Rationale

1. **The access predicate must live in the same SQL statement as the ANN search.** A separate vector DB would require either duplicating the access logic in a second system or post-filtering in Python — exactly what PRD §8.4 item 2 prohibits.
2. **RLS policies apply to the same table** the vectors are stored in. One enforcement mechanism, not two.
3. **Operational simplicity.** One database to back up, monitor, and secure. No sync protocol between a relational DB and a vector DB.
4. **pgvector >= 0.8.0** provides iterative index scans, closing the filtered-ANN recall gap that was the historical weakness of in-database vector search.

## Consequences

- We accept pgvector's performance ceiling (~100K–1M vectors per partition comfortably). For MVP scale this is more than sufficient.
- The HNSW index parameters (`m`, `ef_construction`, `ef_search`) are tunable and must be characterised during Phase A evaluation.
