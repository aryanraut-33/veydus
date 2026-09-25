# ─────────────────────────────────────────────────────────────────
# VEYDUS — Query Rewriting & Standalone Transformation
# ─────────────────────────────────────────────────────────────────
# What:  Reformulates conversational follow-up questions into standalone queries
#        and implements the HLD §3.3 / §14 self-contained query skip heuristic.
# How:   - is_self_contained_query(): Analyzes grammatical signals, pronoun absence,
#          and self-contained syntactic structures.
#        - rewrite_query(): Completely skips rewriting on Turn 1 of a conversation;
#          skips rewriting if the query is already self-contained; calls the inference
#          provider only when disambiguation is necessary.
# Why:   HLD §14 latency budget: Query rewriting adds ~250ms outside the 500ms
#        retrieval subtotal. The heuristic ensures this latency is only incurred when
#        essential for retrieval accuracy.
# Tools: re, logging, veydus.providers.base.InferenceProvider.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from veydus.providers.base import InferenceProvider

logger = logging.getLogger(__name__)

# Pronouns and references requiring conversational context
AMBIGUOUS_PRONOUNS: frozenset[str] = frozenset(
    {
        "it",
        "its",
        "they",
        "them",
        "their",
        "theirs",
        "this",
        "that",
        "these",
        "those",
        "he",
        "him",
        "his",
        "she",
        "her",
        "hers",
    }
)

# Colloquial phrases signaling dependence on prior conversational turns
DEPENDENT_PHRASES: list[re.Pattern[str]] = [
    re.compile(r"\bwhat about\b", re.IGNORECASE),
    re.compile(r"\bhow about\b", re.IGNORECASE),
    re.compile(r"\band also\b", re.IGNORECASE),
    re.compile(r"\bthe second one\b", re.IGNORECASE),
    re.compile(r"\bthe first one\b", re.IGNORECASE),
    re.compile(r"\bthe former\b", re.IGNORECASE),
    re.compile(r"\bthe latter\b", re.IGNORECASE),
    re.compile(r"\bexplain more\b", re.IGNORECASE),
    re.compile(r"\btell me more\b", re.IGNORECASE),
    re.compile(r"\bwhy is that\b", re.IGNORECASE),
    re.compile(r"\bwhat else\b", re.IGNORECASE),
    re.compile(r"\bsame thing\b", re.IGNORECASE),
]


def is_self_contained_query(query: str) -> bool:
    """Heuristic evaluating whether a follow-up query is already standalone.

    Returns:
        True if the query has sufficient context and lacks ambiguous referents,
        allowing the pipeline to bypass the ~250ms rewrite latency (HLD §3.3).
    """
    clean_query = query.strip()
    words = re.findall(r"\b\w+\b", clean_query.lower())

    # Extremely short queries ("Why?", "And then?", "How?") are dependent
    if len(words) < 4:
        return False

    # Check for dependent follow-up phrases
    for pattern in DEPENDENT_PHRASES:
        if pattern.search(clean_query):
            return False

    # Check for ambiguous pronouns that refer back to prior turns
    # If a pronoun appears without an explicit antecedent in the query, it is dependent
    return not any(word in AMBIGUOUS_PRONOUNS for word in words)


async def rewrite_query(
    query: str,
    chat_history: list[dict[str, str]] | None,
    provider: InferenceProvider,
) -> tuple[str, bool]:
    """Transforms an ambiguous conversational query into a standalone query.

    Returns:
        A tuple of (effective_query, was_rewritten: bool).
    """
    clean_query = query.strip()

    # Rule 1: Turn 1 of a conversation never needs rewriting
    if not chat_history:
        return clean_query, False

    # Rule 2: Self-contained follow-ups skip rewriting via heuristic (HLD §3.3)
    if is_self_contained_query(clean_query):
        logger.debug("Query '%s' is self-contained. Skipping rewrite heuristic.", clean_query)
        return clean_query, False

    # Rule 3: Dependent query requires conversational reformulation
    logger.info("Rewriting dependent follow-up query: '%s'", clean_query)

    # Format recent history turns (last 3 turns max for fast rewrite)
    history_lines: list[str] = []
    for turn in chat_history[-6:]:
        role = turn.get("role", "user").capitalize()
        content = turn.get("content", "").strip()
        history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines)

    system_prompt = (
        "You are a query reformulation assistant. Given the recent conversation history "
        "and a follow-up user query, rewrite the user query into a single standalone, "
        "self-contained question that can be understood without the conversation history.\n"
        "Rules:\n"
        "- Do NOT answer the question.\n"
        "- Maintain the user's original intent.\n"
        "- Only return the rewritten query text and nothing else."
    )

    user_prompt = (
        f"Conversation History:\n{history_text}\n\n"
        f"Follow-up Query: {clean_query}\n\n"
        "Standalone Query:"
    )

    try:
        rewritten = await provider.generate(prompt=user_prompt, system_prompt=system_prompt)
        rewritten_clean = rewritten.strip().strip('"').strip("'")
        if rewritten_clean:
            return rewritten_clean, True
    except Exception as exc:
        logger.warning("Query rewrite failed: %s. Falling back to original query.", exc)

    return clean_query, False
