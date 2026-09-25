# ─────────────────────────────────────────────────────────────────
# VEYDUS — Chunking Models & Strategy Interface
# ─────────────────────────────────────────────────────────────────
# What:  Data models representing extracted chunks and the chunking protocol.
# How:   Uses Pydantic v2 for ChunkItem model definition and typing.Protocol
#        for structural subtyping of chunking strategies.
# Why:   HLD §7.2 requires structure-aware chunking preserving section hierarchy,
#        headings, page numbers, and table boundaries before embedding.
# Tools: Pydantic v2 (BaseModel, Field), typing.Protocol.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from veydus.ingestion.parsers.base import ParsedDocument


class ChunkItem(BaseModel):
    """An individual text chunk ready for embedding and indexing."""

    content: str = Field(description="Raw text content of the chunk")
    chunk_index: int = Field(description="Sequential index of the chunk within the document")
    section_heading: str | None = Field(
        default=None, description="Heading of the enclosing section"
    )
    page_number: int | None = Field(default=None, description="Source page number if available")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary chunk metadata (e.g., is_table, tokens)"
    )


@runtime_checkable
class ChunkingStrategy(Protocol):
    """Protocol for chunking strategies that transform ParsedDocuments into ChunkItems."""

    def chunk(self, doc: ParsedDocument) -> list[ChunkItem]:
        """Convert a ParsedDocument into a sequence of ChunkItem objects."""
        ...
