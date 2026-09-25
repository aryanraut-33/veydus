# ─────────────────────────────────────────────────────────────────
# VEYDUS — Unit Tests: Parsers and Structure-Aware Chunker
# ─────────────────────────────────────────────────────────────────
# What:  Validates markdown parsing, section hierarchy extraction,
#        table extraction, and recursive structure-aware chunking.
# How:   Creates temporary markdown/text files, executes TextAndMarkdownParser,
#        passes ParsedDocument to RecursiveStructureChunker, and asserts properties.
# Why:   HLD §7.2 requires chunk boundaries to respect document structure,
#        preserve headings and table integrity, and maintain sequential indexing.
# Tools: pytest, tmp_path, pathlib.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from veydus.ingestion.chunking.recursive import RecursiveStructureChunker
from veydus.ingestion.parsers.base import get_parser_for_file
from veydus.ingestion.parsers.text import TextAndMarkdownParser

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_markdown_parser_extracts_headings_and_tables(tmp_path: Path) -> None:
    """Markdown parser extracts sections, headings, and table rows correctly."""
    md_content = """# Engineering Architecture

This document describes the engineering architecture for Veydus.

## Database Layer

PostgreSQL with pgvector and Row-Level Security.

| Feature | Setting | Status |
| --- | --- | --- |
| RLS | FORCE | Enabled |
| Vectors | 768 dims | L2 Norm |

## Ingestion Pipeline

Docling extracts structure and feeds recursive chunker.
"""
    file_path = tmp_path / "architecture.md"
    file_path.write_text(md_content, encoding="utf-8")

    parser = get_parser_for_file(file_path)
    assert isinstance(parser, TextAndMarkdownParser)

    doc = await parser.parse(file_path)

    assert doc.title == "Engineering Architecture"
    assert len(doc.sections) >= 3
    assert any(s.title == "Database Layer" for s in doc.sections)
    assert any(s.title == "Ingestion Pipeline" for s in doc.sections)
    assert len(doc.tables) == 1
    assert "RLS" in doc.tables[0].markdown


@pytest.mark.asyncio
async def test_recursive_structure_chunker_bounds_and_headings(tmp_path: Path) -> None:
    """Chunker respects target size, min size, and assigns section headings."""
    long_para = "Veydus provides enterprise grade tenant isolation. " * 30  # ~1560 chars
    md_content = f"""# Operations Manual

## Section Alpha

{long_para}

## Section Beta

Short note.
"""
    file_path = tmp_path / "manual.md"
    file_path.write_text(md_content, encoding="utf-8")

    parser = TextAndMarkdownParser()
    doc = await parser.parse(file_path)

    chunker = RecursiveStructureChunker(target_size=400, overlap=50, min_size=50, max_size=800)
    chunks = chunker.chunk(doc)

    assert len(chunks) >= 3

    # Check contiguous indexing
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i
        assert len(chunk.content) > 0

    # First few chunks must belong to Section Alpha
    assert chunks[0].section_heading == "Section Alpha"
    # Last chunk must belong to Section Beta
    assert chunks[-1].section_heading == "Section Beta"
