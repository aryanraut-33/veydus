# ─────────────────────────────────────────────────────────────────
# VEYDUS — Document Parsers Package
# ─────────────────────────────────────────────────────────────────
# What:  Public exports for VEYDUS document parsers and models.
# How:   Exports ParsedDocument, ParsedSection, ParsedTable, ParsedImage,
#        DocumentParser, get_parser_for_file, TextAndMarkdownParser, DoclingParser.
# Why:   HLD §7.2 requires unified parser abstraction for multi-format ingestion.
# Tools: Python module exports.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

from veydus.ingestion.parsers.base import (
    DocumentParser,
    ParsedDocument,
    ParsedImage,
    ParsedSection,
    ParsedTable,
    get_parser_for_file,
)
from veydus.ingestion.parsers.docling import DoclingParser
from veydus.ingestion.parsers.text import TextAndMarkdownParser

__all__ = [
    "DoclingParser",
    "DocumentParser",
    "ParsedDocument",
    "ParsedImage",
    "ParsedSection",
    "ParsedTable",
    "TextAndMarkdownParser",
    "get_parser_for_file",
]
