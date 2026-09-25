# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Matryoshka Normalization Order Invariant
# ─────────────────────────────────────────────────────────────────
# What:  Validates that Matryoshka dimension truncation (2048 -> 768)
#        and L2-normalization strictly follow the required order of operations.
# How:   Uses numpy to generate 2048-dimensional vectors, computes norms,
#        and asserts that truncation-first yields unit norm (1.0 ± 1e-5),
#        while normalization-first produces sub-unitary vectors (< 1.0).
# Why:   HLD §12.2 & ADR-0004 specify that reversing the order breaks the
#        cosine distance invariant in pgvector HNSW indices, degrading retrieval.
# Tools: pytest, numpy (vector norms, random generation).
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import math

import numpy as np
import pytest

from veydus.providers.mock import MockProvider
from veydus.providers.nim import apply_matryoshka_truncation


def test_correct_matryoshka_order_yields_unit_norm() -> None:
    """Truncating first, then L2-normalizing must yield a vector of norm 1.0 ± 1e-5."""
    rng = np.random.default_rng(42)
    raw_2048 = rng.standard_normal(2048).astype(np.float32)

    result_768 = apply_matryoshka_truncation(raw_2048, target_dim=768)

    assert len(result_768) == 768
    norm = float(np.linalg.norm(result_768))
    assert math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5)


def test_reversed_matryoshka_order_fails_unit_norm() -> None:
    """Normalizing first, then truncating breaks unit norm (norm < 1.0).

    This test confirms that reversing the operations degrades the vector
    and proves why our implementation in apply_matryoshka_truncation is required.
    """
    rng = np.random.default_rng(42)
    raw_2048 = rng.standard_normal(2048).astype(np.float32)

    # Flawed approach: normalize first, then truncate without re-normalizing
    normalized_2048 = raw_2048 / np.linalg.norm(raw_2048)
    truncated_flawed = normalized_2048[:768]

    flawed_norm = float(np.linalg.norm(truncated_flawed))

    # For random isotropic 2048-d vectors, truncating to 768 has expected norm sqrt(768/2048) ≈ 0.612
    assert flawed_norm < 0.95
    assert not math.isclose(flawed_norm, 1.0, rel_tol=1e-3)


def test_zero_vector_edge_case() -> None:
    """A zero vector should not cause a ZeroDivisionError."""
    zero_vec = np.zeros(2048, dtype=np.float32)
    result = apply_matryoshka_truncation(zero_vec, target_dim=768)
    assert len(result) == 768
    assert all(x == 0.0 for x in result)


@pytest.mark.asyncio
async def test_mock_provider_produces_768_dim_unit_vectors() -> None:
    """MockProvider must produce deterministic 768-dim unit-normalized vectors."""
    provider = MockProvider(dimensions=768)
    texts = [
        "Engineering safety report for plant A",
        "Q3 financial performance overview",
    ]

    embeddings = await provider.embed(texts)

    assert len(embeddings) == 2
    for emb in embeddings:
        assert len(emb) == 768
        norm = float(np.linalg.norm(emb))
        assert math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5)

    # Determinism check: embedding the same text twice must yield identical vectors
    embeddings_repeat = await provider.embed(["Engineering safety report for plant A"])
    assert embeddings[0] == embeddings_repeat[0]
