# ─────────────────────────────────────────────────────────────────
# VEYDUS — Text & Markdown Document Parser
# ─────────────────────────────────────────────────────────────────
# What:  Lightweight parser for plain text (.txt) and Markdown (.md) documents.
# How:   Uses regular expressions to detect Markdown heading levels (#, ##, ###),
#        partition document flow into semantic sections, and extract Markdown tables.
# Why:   HLD §7.2 requires structured extraction preserving document hierarchy
#        for structured chunking without unnecessary heavy OCR overhead.
# Tools: re (regular expressions), pathlib, asyncio.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from veydus.ingestion.parsers.base import (
    ParsedDocument,
    ParsedSection,
    ParsedTable,
)

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
TABLE_LINE_PATTERN = re.compile(r"^\s*\|.*\|\s*$")


class TextAndMarkdownParser:
    """Parser for plain text (.txt) and markdown (.md) documents."""

    async def parse(self, file_path: Path, mime_type: str | None = None) -> ParsedDocument:
        """Parse text or markdown file asynchronously."""
        # Read file in threadpool to avoid blocking async event loop
        text = await asyncio.to_thread(file_path.read_text, encoding="utf-8", errors="replace")

        if file_path.suffix.lower() in {".md", ".markdown"}:
            return self._parse_markdown(file_path.name, text)
        return self._parse_plain_text(file_path.name, text)

    def _parse_plain_text(self, filename: str, text: str) -> ParsedDocument:
        """Parse plain text file into a single top-level section."""
        cleaned = text.strip()
        section = ParsedSection(
            title=Path(filename).stem,
            level=1,
            content=cleaned,
            page_number=1,
        )
        return ParsedDocument(
            title=Path(filename).stem,
            sections=[section] if cleaned else [],
            tables=[],
            images=[],
            raw_text=cleaned,
            metadata={"filename": filename, "format": "text"},
        )

    def _parse_markdown(self, filename: str, text: str) -> ParsedDocument:
        """Parse markdown text into structured sections and tables."""
        sections: list[ParsedSection] = []
        tables: list[ParsedTable] = []

        lines = text.splitlines()
        current_title = Path(filename).stem
        current_level = 1
        current_content_lines: list[str] = []
        doc_title = current_title

        # Check for first H1 to use as document title
        for line in lines:
            match = HEADING_PATTERN.match(line)
            if match and len(match.group(1)) == 1:
                doc_title = match.group(2).strip()
                break

        # Extract markdown tables if present
        table_buffer: list[str] = []
        for line in lines:
            if TABLE_LINE_PATTERN.match(line):
                table_buffer.append(line)
            else:
                if len(table_buffer) >= 2:
                    table_md = "\n".join(table_buffer)
                    # Simple row extraction
                    rows = [
                        [c.strip() for c in r.strip("|").split("|")]
                        for r in table_buffer
                        if not re.match(r"^\s*\|?\s*:?-+:?\s*(\|?\s*:?-+:?\s*)*\|?\s*$", r)
                    ]
                    tables.append(ParsedTable(markdown=table_md, rows=rows, page_number=1))
                table_buffer = []
        if len(table_buffer) >= 2:
            table_md = "\n".join(table_buffer)
            rows = [
                [c.strip() for c in r.strip("|").split("|")]
                for r in table_buffer
                if not re.match(r"^\s*\|?\s*:?-+:?\s*(\|?\s*:?-+:?\s*)*\|?\s*$", r)
            ]
            tables.append(ParsedTable(markdown=table_md, rows=rows, page_number=1))

        # Split into sections by heading
        for line in lines:
            match = HEADING_PATTERN.match(line)
            if match:
                # Flush previous section
                content_str = "\n".join(current_content_lines).strip()
                if content_str or current_content_lines:
                    sections.append(
                        ParsedSection(
                            title=current_title,
                            level=current_level,
                            content=content_str,
                            page_number=1,
                        )
                    )
                current_level = len(match.group(1))
                current_title = match.group(2).strip()
                current_content_lines = []
            else:
                current_content_lines.append(line)

        # Flush final section
        content_str = "\n".join(current_content_lines).strip()
        if content_str or not sections:
            sections.append(
                ParsedSection(
                    title=current_title,
                    level=current_level,
                    content=content_str,
                    page_number=1,
                )
            )

        return ParsedDocument(
            title=doc_title,
            sections=sections,
            tables=tables,
            images=[],
            raw_text=text.strip(),
            metadata={"filename": filename, "format": "markdown"},
        )
