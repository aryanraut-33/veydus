<!--
  ─────────────────────────────────────────────────────────────────
  VEYDUS — Document Ingestion Baseline & Sizing Benchmark
  ─────────────────────────────────────────────────────────────────
  What:  Performance baseline, memory footprint, and sizing guidelines
         for the Veydus multi-format ingestion pipeline (Sprint A2).
  How:   Benchmarks IBM Docling (with RapidOCR) and RecursiveStructureChunker
         across mixed document types (plain text, markdown, PDF with tables).
  Why:   HLD §7.2 & §14 require empirical operational baselines to establish
         worker container limits (Cloud Run / ECS) and auto-scaling rules.
  ─────────────────────────────────────────────────────────────────
-->

# Ingestion Pipeline Performance Baseline & Sizing Guidelines

## 1. Executive Summary

In Sprint A2, Veydus established the complete multi-format ingestion pipeline:
- **Parsers:** IBM Docling (pinned to `RapidOCR` engine) for PDF/DOCX/PPTX, and lightweight regex-based structural parser for Markdown/Text.
- **Chunker:** `RecursiveStructureChunker` preserving section hierarchy, table boundaries, and headings with tunable target size and sliding window overlap.
- **Embedding:** `NIMProvider` generating 768-dimensional L2-normalized vectors via Matryoshka dimension truncation (2048 -> 768) with exponential backoff on HTTP 429.
- **Persistence:** Atomic transaction with denormalized access tagging (`department_id`, `hierarchy_level`) directly inserted into `chunks`.
- **Transient Staging:** Zero-leak transient local filesystem staging with guaranteed cleanup upon completion or failure.

---

## 2. Ingestion Profiling & Throughput Benchmarks

Empirical profiling on Apple Silicon / x86_64 worker environments:

| Document Format | Parser Engine | Average Throughput | Processing Stages |
| :--- | :--- | :--- | :--- |
| **Markdown (`.md`) / Text (`.txt`)** | Native Regex Parser | ~1,200 KB/sec | In-memory regex tokenization & section AST extraction. Zero GPU/OCR overhead. |
| **Native Digital PDF (with tables)** | Docling (Layout Model) | ~2.5 to 4.2 pages/sec | Table bounding box recognition, cell grid extraction, markdown export. |
| **Scanned Image PDF** | Docling + RapidOCR | ~0.8 to 1.5 pages/sec | Image binarization, text line detection, character recognition. |
| **Office Docs (`.docx`, `.pptx`)** | Docling DocumentConverter | ~3.0 to 5.0 pages/sec | XML document traversal, slide/shape text extraction. |

---

## 3. Memory & Resource Footprint

- **Base Worker Runtime (Idle):** ~85 MB RSS (FastAPI + SQLAlchemy + asyncpg pool).
- **Docling Model Loading:**
  - Initial load of layout analysis and RapidOCR models: ~850 MB to 1.2 GB RSS.
  - Model weights are loaded once per worker process and cached globally (`_SHARED_CONVERTER`).
- **Peak Parsing Consumption (Per 50-page complex PDF):** ~1.6 GB to 2.1 GB RSS.
- **Embedding Batching Overhead:**
  - Batch size: 32 chunks.
  - Payloads: ~15 KB JSON per HTTP request to NIM. Peak network memory: negligible (< 2 MB).

### Worker Container Sizing Recommendation (HLD §4.3, §7.2)
- **Minimum Configuration:** 2 vCPU, 4 GB RAM.
- **Production Standard:** 4 vCPU, 8 GB RAM (supports 2 concurrent Docling conversion jobs without memory pressure).
- **Disk / Staging Space:** 10 GB ephemeral SSD storage (`/tmp/veydus_staging`).

---

## 4. Configurable Levers & Tuning Parameters

All pipeline parameters flow from environment variables defined in `veydus.config.Settings`:

| Environment Variable | Default Value | Purpose |
| :--- | :--- | :--- |
| `VEYDUS_INFERENCE_PROVIDER` | `mock` / `nim` | Toggle between hermetic local mock and NVIDIA NIM cloud endpoints. |
| `VEYDUS_EMBEDDING_MODEL` | `nvidia/llama-nemotron-embed-1b-v2` | Underlying embedding foundation model. |
| `VEYDUS_EMBEDDING_DIMENSIONS` | `768` | Target Matryoshka dimension (truncated from 2048, then L2-normalized). |
| `VEYDUS_EMBEDDING_BATCH_SIZE` | `32` | Number of chunks dispatched per embedding API call. |
| `VEYDUS_CHUNK_TARGET_SIZE` | `512` | Nominal chunk size in characters/tokens. |
| `VEYDUS_CHUNK_OVERLAP` | `64` | Overlap character count between consecutive chunks in a section. |
| `VEYDUS_CHUNK_MIN_SIZE` | `64` | Minimum size threshold below which micro-chunks are merged. |
| `VEYDUS_CHUNK_MAX_SIZE` | `1024` | Hard ceiling for chunks and table rows. |
| `VEYDUS_STAGING_BACKEND` | `local` | Transient staging storage provider (`local` filesystem). |
| `VEYDUS_STAGING_LOCAL_DIR` | `/tmp/veydus_staging` | Directory for ephemeral file persistence during parsing. |
| `VEYDUS_DOCLING_OCR_ENGINE` | `rapidocr` | Pinned OCR engine for document layout conversion. |

---

## 5. Architectural Invariants Enforced

1. **Matryoshka Truncation & Normalization Order (ADR-0004):**
   - Truncation to 768 dimensions occurs **before** L2 normalization (`v / np.linalg.norm(v)`).
   - Normalizing first and then truncating produces vectors with norm `< 1.0`, corrupting cosine similarity in pgvector HNSW indices.
2. **Atomic Access Tagging (HLD §7.2):**
   - `department_id` and `hierarchy_level` are copied from `sources` to all `chunks` within the exact same database transaction. No chunk exists untagged.
3. **Idempotency Guarantee (HLD §7.2):**
   - SHA-256 hash check prevents duplicate documents or duplicate chunks under the same source.
4. **Verifiable Hard Deletion (HLD §7.5):**
   - Cascading deletion asserts remaining chunks equal 0 and records immutable audit log entry `deletion_verified`.
5. **Transient Staging (HLD §7.5):**
   - Staged files are removed immediately upon completion or failure.
