# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Token-Budgeted Conversation Memory (HLD §9.2)
# ─────────────────────────────────────────────────────────────────
# What:  Unit tests verifying conversation history budget enforcement (2000 tokens),
#        minimum 4 verbatim turns retention, rolling summary generation, and budget-triggered
#        (not per-turn) summarization cadence.
# How:   Simulates multi-turn conversations (4 turns and 20 turns) against
#        assemble_conversation_history() with a tracked MockProvider.
# Why:   HLD §9.2 Success Check 6: Enforces bounded input growth to prevent
#        LLM prompt context overflow and runaway inference cost.
# Tools: pytest, unittest.mock (AsyncMock), veydus.rag.conversation.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from veydus.providers.mock import MockProvider
from veydus.rag.conversation import (
    MessageTurn,
    approx_token_count,
    assemble_conversation_history,
)


def test_approx_token_count() -> None:
    text = "The quick brown fox jumps over the lazy dog."
    tokens = approx_token_count(text)
    assert tokens > 5
    assert approx_token_count("") == 0


@pytest.mark.asyncio
async def test_short_conversation_fits_in_budget() -> None:
    provider = MockProvider()
    mock_generate = AsyncMock()
    provider.generate = mock_generate  # type: ignore[method-assign]

    turns = [
        MessageTurn(ordinal=1, role="user", content="Hello, what is Veydus?"),
        MessageTurn(ordinal=2, role="assistant", content="Veydus is a hierarchical RAG platform."),
        MessageTurn(ordinal=3, role="user", content="Where is documentation stored?"),
        MessageTurn(ordinal=4, role="assistant", content="In the company engineering repository."),
    ]

    assembled = await assemble_conversation_history(
        messages=turns,
        existing_summary=None,
        summary_upto_ordinal=0,
        history_budget=2000,
        min_verbatim_turns=4,
        provider=provider,
    )

    # Fits within budget: all 4 turns verbatim, 0 summarization calls
    assert assembled.verbatim_turn_count == 4
    assert len(assembled.formatted_turns) == 4
    assert assembled.total_tokens < 2000
    assert assembled.summary_was_updated is False
    mock_generate.assert_not_called()


@pytest.mark.asyncio
async def test_twenty_turn_conversation_budget_enforcement() -> None:
    provider = MockProvider()
    mock_generate = AsyncMock(
        return_value="Summary of prior discussion about quarterly company policies."
    )
    provider.generate = mock_generate  # type: ignore[method-assign]

    # Build 20 substantial turns (~180 tokens each = ~3600 tokens total, exceeds 2000 budget)
    turns: list[MessageTurn] = []
    paragraph = (
        "Enterprise access governance policy requires department level assignment and multi-factor "
        "authentication across all systems. Personnel must verify clearances on an annual basis with HR "
        "and legal departments to maintain active directory roles. Confidential operational playbooks "
        "must be audited quarterly and access tokens revoked immediately upon employee transfer or departure. "
    )
    long_content = paragraph * 3  # ~180 tokens per turn
    for i in range(1, 21):
        role = "user" if i % 2 == 1 else "assistant"
        turns.append(
            MessageTurn(
                ordinal=i,
                role=role,
                content=f"Turn {i}: {long_content}",
            )
        )

    assembled = await assemble_conversation_history(
        messages=turns,
        existing_summary=None,
        summary_upto_ordinal=0,
        history_budget=2000,
        min_verbatim_turns=4,
        provider=provider,
    )

    # Success check 6 invariants:
    # 1. Assembled prompt history stays within 2000-token budget
    assert assembled.total_tokens <= 2000, (
        f"Assembled tokens ({assembled.total_tokens}) exceeded budget 2000!"
    )

    # 2. Retains at least 4 verbatim turns
    assert assembled.verbatim_turn_count >= 4

    # 3. Exactly 1 summarization call triggered (not one per turn)
    assert assembled.summary_was_updated is True
    mock_generate.assert_called_once()

    # 4. Summary is prepended to formatted turns
    assert assembled.formatted_turns[0]["role"] == "system"
    assert "Previous Conversation Summary" in assembled.formatted_turns[0]["content"]

    # 5. Last turn is preserved verbatim
    assert assembled.formatted_turns[-1]["content"] == turns[-1].content
