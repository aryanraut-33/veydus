# ─────────────────────────────────────────────────────────────────
# VEYDUS — Application Configuration
# ─────────────────────────────────────────────────────────────────
# What:  Centralised settings loaded from environment variables.
# How:   Uses pydantic-settings to read env vars with type
#        validation, defaults for local dev, and a .env file
#        fallback.  Every configurable value in the system flows
#        through this module — no scattered os.getenv() calls.
# Why:   HLD §7.4 marks several values as "tunable" (chunk size,
#        retrieval k, ef_search, etc.).  Centralising them here
#        makes tuning a config change, not a code change.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, populated from environment variables.

    Every field maps to an env var with the same name (case-insensitive)
    with optional VEYDUS_ prefix or explicit standard aliases.
    """

    model_config = SettingsConfigDict(
        env_prefix="VEYDUS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Database ─────────────────────────────────────────────────
    # Runtime connection (veydus_app role, subject to RLS)
    app_db_url: str = "postgresql+asyncpg://veydus_app:veydus_app_pw@localhost:5432/veydus"
    # Migration connection (veydus_migrate role, BYPASSRLS)
    migrate_db_url: str = "postgresql://veydus_migrate:veydus_migrate_pw@localhost:5432/veydus"

    # ── Authentication & Security ────────────────────────────────
    jwt_secret_key: str = Field(
        default="insecure_dev_jwt_secret_key_change_in_production",
        validation_alias=AliasChoices("JWT_SECRET_KEY", "VEYDUS_JWT_SECRET_KEY"),
    )

    # ── Connection Pool ──────────────────────────────────────────
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # ── Inference & Models (HLD §12) ─────────────────────────────
    inference_provider: str = Field(
        default="nim",
        validation_alias=AliasChoices("INFERENCE_PROVIDER", "VEYDUS_INFERENCE_PROVIDER"),
    )
    nvidia_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("NVIDIA_API_KEY", "VEYDUS_NVIDIA_API_KEY"),
    )
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    embedding_model: str = "nvidia/llama-nemotron-embed-1b-v2"
    embedding_dimensions: int = 768
    embedding_batch_size: int = 32
    generation_model: str = "meta/llama-3.1-70b-instruct"

    # ── Chunking (HLD §7.4, tunable) ─────────────────────────────
    chunk_target_size: int = 512
    chunk_overlap: int = 64
    chunk_min_size: int = 64
    chunk_max_size: int = 1024

    # ── Ingestion & Staging (HLD §7.1, §7.3) ──────────────────────
    staging_backend: str = "local"
    staging_local_dir: str = "/tmp/veydus_staging"
    docling_ocr_engine: str = "rapidocr"  # Pinned explicitly per HLD §7.3

    # ── Retrieval & Indexing (HLD §6.3, §6.4, tunable) ───────────
    retrieval_k: int = 30
    retrieval_ef_search: int = 100
    retrieval_max_scan_tuples: int = 20000

    # ── Reranking & Context (HLD §8.2, §15, tunable) ─────────────
    rerank_n: int = 6
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ── Grounding & Safety (HLD §5.2, §8.4, §8.5) ────────────────
    grounding_threshold: float = 0.30
    refusal_message: str = "No information available, or it exists above your access level."
    pii_redaction_enabled: bool = True

    # ── Application ──────────────────────────────────────────────
    debug: bool = False
    log_level: str = "INFO"


# Module-level singleton — import this, don't instantiate Settings again.
settings = Settings()
