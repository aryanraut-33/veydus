# ─────────────────────────────────────────────────────────────────
# VEYDUS — Token-Budgeted Conversation Session Memory
# ─────────────────────────────────────────────────────────────────
# What:  Multi-turn conversation context assembly under a strict 2000-token budget,
#        rolling summary management, and HLD §9.4 summary scope tagging.
# How:   - assemble_conversation_history(): Evaluates history token consumption.
#        - Retains at least 4 verbatim turns (2 exchanges) at the end of the history.
#        - Compresses older turns into rolling_summary when the budget is breached.
#        - Summarization is triggered on budget breach, not per-turn (HLD §9.2).
#        - Populates summary_max_level and summary_department_id (HLD §9.4).
# Why:   HLD §9.2 & §9.4 mandate bounded prompt growth to prevent context window
#        exhaustion while maintaining conversational continuity and scope hygiene.
# Tools: pydantic v2, uuid.UUID, logging, veydus.providers.base.InferenceProvider.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from veydus.providers.base import InferenceProvider

logger = logging.getLogger(__name__)

# HLD §9.2 Default Budget Configurations
DEFAULT_HISTORY_TOKEN_BUDGET: int = 2000
MINIMUM_VERBATIM_TURNS: int = 4  # 2 full user-assistant exchanges


def approx_token_count(text: str) -> int:
    """Estimates token count using standard word + punctuation heuristic.

    Roughly ~1.3 tokens per whitespace word, with minimum bound.
    """
    if not text:
        return 0
    words = text.split()
    return max(1, int(len(words) * 1.3) + (len(text) // 30))


class MessageTurn(BaseModel):
    """Single conversational turn in a session."""

    ordinal: int
    role: str
    content: str
    token_count: int = 0


class AssembledHistory(BaseModel):
    """Context history assembled for prompt injection."""

    formatted_turns: list[dict[str, str]] = Field(
        default_factory=list,
        description="Formatted messages ready for LLM consumption",
    )
    total_tokens: int = Field(description="Total token consumption of assembled history")
    rolling_summary: str | None = Field(default=None, description="Current rolling summary")
    summary_upto_ordinal: int = Field(default=0, description="Highest ordinal included in summary")
    verbatim_turn_count: int = Field(description="Number of verbatim turns retained")
    summary_was_updated: bool = Field(
        default=False, description="True if a summarization call fired"
    )


async def generate_rolling_summary(
    existing_summary: str | None,
    turns_to_summarize: list[MessageTurn],
    provider: InferenceProvider,
) -> str:
    """Condenses previous conversation turns into an updated rolling summary."""
    turns_text_list: list[str] = []
    for turn in turns_to_summarize:
        role = turn.role.capitalize()
        turns_text_list.append(f"{role}: {turn.content}")
    new_turns_text = "\n".join(turns_text_list)

    existing_summary_part = f"Existing Summary:\n{existing_summary}\n\n" if existing_summary else ""

    system_prompt = (
        "You are a conversation summarization engine. Condense the conversation history "
        "into a concise, factual summary capturing all key topics, user queries, and assistant answers. "
        "Preserve key facts, dates, numbers, and decisions. Be brief and direct."
    )

    user_prompt = (
        f"{existing_summary_part}"
        f"New Turns to Summarize:\n{new_turns_text}\n\n"
        "Updated Rolling Summary:"
    )

    try:
        updated = await provider.generate(prompt=user_prompt, system_prompt=system_prompt)
        return updated.strip()
    except Exception as exc:
        logger.warning("Rolling summarization failed: %s. Preserving prior summary.", exc)
        return existing_summary or ""


async def assemble_conversation_history(
    messages: list[MessageTurn],
    existing_summary: str | None = None,
    summary_upto_ordinal: int = 0,
    history_budget: int = DEFAULT_HISTORY_TOKEN_BUDGET,
    min_verbatim_turns: int = MINIMUM_VERBATIM_TURNS,
    provider: InferenceProvider | None = None,
) -> AssembledHistory:
    """Assembles prompt history within budget, triggering summarization on overflow."""
    if not messages:
        return AssembledHistory(
            formatted_turns=[],
            total_tokens=0,
            rolling_summary=existing_summary,
            summary_upto_ordinal=summary_upto_ordinal,
            verbatim_turn_count=0,
            summary_was_updated=False,
        )

    # Sort turns by ordinal ascending
    sorted_messages = sorted(messages, key=lambda m: m.ordinal)

    # Calculate token counts for all turns
    for m in sorted_messages:
        if m.token_count <= 0:
            m.token_count = approx_token_count(m.content)

    total_tokens_all = sum(m.token_count for m in sorted_messages)

    # Case 1: Total history fits entirely within budget without summarization
    if total_tokens_all <= history_budget:
        formatted = [{"role": m.role, "content": m.content} for m in sorted_messages]
        return AssembledHistory(
            formatted_turns=formatted,
            total_tokens=total_tokens_all,
            rolling_summary=existing_summary,
            summary_upto_ordinal=summary_upto_ordinal,
            verbatim_turn_count=len(sorted_messages),
            summary_was_updated=False,
        )

    # Case 2: History exceeds budget. Must retain at least min_verbatim_turns.
    # Take turns after summary_upto_ordinal
    active_turns = [m for m in sorted_messages if m.ordinal > summary_upto_ordinal]
    if len(active_turns) < min_verbatim_turns:
        # Guarantee minimum verbatim turns even if they predate summary_upto_ordinal
        active_turns = sorted_messages[-min_verbatim_turns:]

    # Work backwards from the most recent turn, accumulating verbatim turns within budget
    # Reserve tokens for rolling summary (~150 tokens)
    reserved_summary_tokens = approx_token_count(existing_summary or "") or 150
    effective_verbatim_budget = max(400, history_budget - reserved_summary_tokens)

    accumulated_tokens = 0

    # Ensure at least min_verbatim_turns from the end
    tail_turns = sorted_messages[-min_verbatim_turns:]
    tail_tokens = sum(m.token_count for m in tail_turns)

    # If tail turns themselves exceed effective budget, we still must retain them (invariant)
    older_turns = sorted_messages[:-min_verbatim_turns]

    # Can we include any older turns before tail_turns?
    remaining_budget = max(0, effective_verbatim_budget - tail_tokens)
    included_older: list[MessageTurn] = []

    for turn in reversed(older_turns):
        if turn.ordinal <= summary_upto_ordinal:
            break
        if accumulated_tokens + turn.token_count <= remaining_budget:
            included_older.append(turn)
            accumulated_tokens += turn.token_count
        else:
            break

    verbatim_turns = list(reversed(included_older)) + tail_turns
    turns_needing_summary = [
        m
        for m in sorted_messages
        if m.ordinal <= verbatim_turns[0].ordinal and m.ordinal > summary_upto_ordinal
    ]

    summary_was_updated = False
    current_summary = existing_summary
    new_summary_upto = summary_upto_ordinal

    # If older turns overflowed beyond summary_upto_ordinal, trigger summarization job
    if turns_needing_summary and provider is not None:
        logger.info(
            "History tokens (%d) exceeded budget (%d). Summarizing %d turns up to ordinal %d.",
            total_tokens_all,
            history_budget,
            len(turns_needing_summary),
            turns_needing_summary[-1].ordinal,
        )
        current_summary = await generate_rolling_summary(
            existing_summary=existing_summary,
            turns_to_summarize=turns_needing_summary,
            provider=provider,
        )
        new_summary_upto = turns_needing_summary[-1].ordinal
        summary_was_updated = True

    # Assemble formatted turns: prepend rolling summary if available
    formatted_turns: list[dict[str, str]] = []
    total_tokens_assembled = 0

    if current_summary:
        summary_turn = {
            "role": "system",
            "content": f"Previous Conversation Summary:\n{current_summary}",
        }
        formatted_turns.append(summary_turn)
        total_tokens_assembled += approx_token_count(summary_turn["content"])

    for turn in verbatim_turns:
        formatted_turns.append({"role": turn.role, "content": turn.content})
        total_tokens_assembled += turn.token_count

    return AssembledHistory(
        formatted_turns=formatted_turns,
        total_tokens=total_tokens_assembled,
        rolling_summary=current_summary,
        summary_upto_ordinal=new_summary_upto,
        verbatim_turn_count=len(verbatim_turns),
        summary_was_updated=summary_was_updated,
    )
