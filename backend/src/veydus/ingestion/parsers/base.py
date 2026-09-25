# ─────────────────────────────────────────────────────────────────
# VEYDUS — Document Parser Data Models & Base Interface
# ─────────────────────────────────────────────────────────────────
# What:  Data structures representing parsed document elements (sections,
#        tables, images, metadata) and the parser protocol/factory.
# How:   Uses Pydantic v2 BaseModels for type safety, validation, and JSON
#        serializability.  Provides an abstract DocumentParser protocol
#        and a factory function that resolves file extensions to parsers.
# Why:   HLD §7.2 requires structured extraction from mixed document formats
#        (PDF, DOCX, PPTX, Markdown, Text) preserving document hierarchy,
#        headings, and tables for structure-aware chunking.
# Tools: Pydantic v2 (BaseModel, Field), pathlib, typing.Protocol.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from pathlib import Path


class ParsedSection(BaseModel):
    """A semantic section within a document, bounded by a heading."""

    title: str = Field(default="")
    level: int = Field(default=1, description="Heading level: 1 for H1, 2 for H2, etc.")
    content: str = Field(default="", description="Text content under this section")
    page_number: int | None = Field(default=None, description="Starting page number if applicable")


class ParsedTable(BaseModel):
    """A structured table extracted from a document."""

    caption: str | None = Field(default=None)
    markdown: str = Field(description="Markdown-formatted representation of the table")
    rows: list[list[str]] = Field(default_factory=list, description="Raw 2D grid of cell values")
    page_number: int | None = Field(default=None)


class ParsedImage(BaseModel):
    """An image extracted from a document with optional caption/OCR text."""

    caption: str | None = Field(default=None)
    page_number: int | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParsedDocument(BaseModel):
    """Normalized document representation resulting from parser execution."""

    title: str = Field(default="")
    sections: list[ParsedSection] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)
    images: list[ParsedImage] = Field(default_factory=list)
    raw_text: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class DocumentParser(Protocol):
    """Protocol for document format parsers."""

    async def parse(self, file_path: Path, mime_type: str | None = None) -> ParsedDocument:
        """Parse a local file into a structured ParsedDocument."""
        ...


def get_parser_for_file(file_path: Path) -> DocumentParser:
    """Resolve and instantiate the appropriate DocumentParser for a given file."""
    ext = file_path.suffix.lower()

    if ext in {".txt", ".md", ".markdown"}:
        from veydus.ingestion.parsers.text import TextAndMarkdownParser

        return TextAndMarkdownParser()
    elif ext in {".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg", ".tiff"}:
        from veydus.ingestion.parsers.docling import DoclingParser

        return DoclingParser()
    else:
        # Default fallback to text parser for generic text-readable files
        from veydus.ingestion.parsers.text import TextAndMarkdownParser

        return TextAndMarkdownParser()
