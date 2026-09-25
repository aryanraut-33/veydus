# VEYDUS

Hierarchical, access-controlled RAG for enterprises. A user's department and clearance level determine exactly which knowledge reaches the model — enforced at the database layer, not in application code.

## Architecture

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy (async), PostgreSQL 16 + pgvector
- **Frontend:** Next.js + TypeScript + Tailwind (Phase B)
- **Inference:** NVIDIA NIM hosted (embedding + generation)
- **Auth:** GCP Identity Platform
- **Infra:** GCP free tier — Cloud Run, Compute Engine, Cloud Tasks, GCS

## Quick Start (Local Development)

```bash
# 1. Start PostgreSQL + pgvector
docker compose up -d

# 2. Install the backend package
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 3. Run database migrations
alembic upgrade head

# 4. Seed fixture data (two orgs, departments, users)
python scripts/seed_fixture.py

# 5. Run tests
pytest
```

## Project Structure

```
veydus/
├── backend/
│   ├── pyproject.toml          # Python deps, ruff, mypy, pytest config
│   ├── alembic/                # Database migrations
│   ├── src/veydus/             # Application source
│   │   ├── api/                # FastAPI routers
│   │   ├── auth/               # JWT verification, scope resolution
│   │   ├── authz/              # Access predicate construction
│   │   ├── rag/                # Retrieval-augmented generation pipeline
│   │   ├── ingestion/          # Document parsing, chunking, embedding
│   │   ├── providers/          # Inference provider abstraction (NIM)
│   │   ├── db/                 # Engine, session, repositories
│   │   ├── observability/      # Tracing, metrics, logging
│   │   └── audit/              # Audit log writes
│   ├── tests/
│   │   ├── isolation/          # Adversarial access-control tests
│   │   ├── integration/        # API + DB tests
│   │   ├── unit/               # Pure logic tests
│   │   └── eval/               # RAGAS evaluation harness
│   └── scripts/                # CLI tools (org provisioning, seeding)
├── frontend/                   # Next.js app (Phase B)
├── infra/terraform/            # GCP infrastructure
├── docs/adr/                   # Architecture Decision Records
└── docker-compose.yml          # Local Postgres + pgvector
```

## Key Design Decisions

See [`docs/adr/`](docs/adr/) for the full list. Highlights:

1. **pgvector over a dedicated vector DB** — the access filter must be in the same SQL statement as the ANN search
2. **RLS for tenant isolation** — enforced at the database, not in application `WHERE` clauses
3. **ABAC now, ReBAC migration path later** — department + level is sufficient for MVP
4. **Hosted NIM behind a provider abstraction** — swap to self-hosted without code changes
5. **Scope-version conversation invalidation** — scope changes immediately invalidate open sessions
