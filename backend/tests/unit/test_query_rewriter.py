# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Query Rewriting & Skip Heuristic
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying standalone query reformulation and the
#        HLD §3.3 / §14 self-contained query skip heuristic.
# How:   Mocks InferenceProvider and evaluates rewrite_query() across:
#        1. First turn of a conversation (must skip without LLM call)
#        2. Dependent follow-up query (e.g. "what about the second one?" - must rewrite)
#        3. Self-contained follow-up query (must skip without LLM call)
# Why:   HLD §14 Success Check 5: Guarantees that the ~250ms query rewrite latency
#        is avoided whenever possible, meeting performance budget requirements.
# Tools: pytest, unittest.mock (AsyncMock), veydus.rag.rewrite.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from veydus.providers.mock import MockProvider
from veydus.rag.rewrite import is_self_contained_query, rewrite_query


def test_is_self_contained_query_heuristics() -> None:
    # Dependent queries
    assert not is_self_contained_query("What about the second one?")
    assert not is_self_contained_query("How about it?")
    assert not is_self_contained_query("Why is that?")
    assert not is_self_contained_query("Can they access this?")
    assert not is_self_contained_query("Tell me more")

    # Self-contained standalone queries
    assert is_self_contained_query(
        "What is the corporate travel expense reimbursement policy in 2026?"
    )
    assert is_self_contained_query(
        "Where are the engineering guidelines for Kubernetes deployments?"
    )
    assert is_self_contained_query("List all approved leave policies for full-time employees")


@pytest.mark.asyncio
async def test_turn_one_skips_rewrite() -> None:
    provider = MockProvider()
    mock_generate = AsyncMock(return_value="Rewritten question")
    provider.generate = mock_generate  # type: ignore[method-assign]

    query = "What about the second one?"
    result, was_rewritten = await rewrite_query(query, chat_history=[], provider=provider)

    assert result == query
    assert was_rewritten is False
    mock_generate.assert_not_called()


@pytest.mark.asyncio
async def test_dependent_followup_triggers_rewrite() -> None:
    provider = MockProvider()
    mock_generate = AsyncMock(return_value="What is the second vacation policy option?")
    provider.generate = mock_generate  # type: ignore[method-assign]

    history = [
        {"role": "user", "content": "Tell me about vacation policies."},
        {"role": "assistant", "content": "There are two options: standard and unlimited."},
    ]

    query = "What about the second one?"
    result, was_rewritten = await rewrite_query(query, chat_history=history, provider=provider)

    assert result == "What is the second vacation policy option?"
    assert was_rewritten is True
    mock_generate.assert_called_once()


@pytest.mark.asyncio
async def test_self_contained_followup_skips_rewrite() -> None:
    provider = MockProvider()
    mock_generate = AsyncMock(return_value="Rewritten question")
    provider.generate = mock_generate  # type: ignore[method-assign]

    history = [
        {"role": "user", "content": "Tell me about vacation policies."},
        {"role": "assistant", "content": "We offer standard and unlimited options."},
    ]

    query = "What is the corporate reimbursement limit for business travel expenses in 2026?"
    result, was_rewritten = await rewrite_query(query, chat_history=history, provider=provider)

    assert result == query
    assert was_rewritten is False
    mock_generate.assert_not_called()
