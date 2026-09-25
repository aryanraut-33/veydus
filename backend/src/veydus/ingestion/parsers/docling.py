# ─────────────────────────────────────────────────────────────────
# VEYDUS — Docling Document Parser (PDF, DOCX, PPTX, Images)
# ─────────────────────────────────────────────────────────────────
# What:  Multi-format document parser extracting structured text, headings,
#        tables, and metadata from rich document formats.
# How:   Uses IBM Docling's DocumentConverter with RapidOCR pinned as the
#        OCR engine (configured via Settings).  Runs CPU/GPU-intensive parsing
#        in worker threads via asyncio.to_thread to maintain an unblocked event loop.
# Why:   HLD §7.2 specifies Docling with RapidOCR for PDF, DOCX, and PPTX
#        ingestion to capture layout hierarchy, tables, and embedded text.
# Tools: docling (DocumentConverter, PdfPipelineOptions, RapidOcrOptions),
#        pathlib, asyncio, Pydantic v2.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from veydus.ingestion.parsers.base import (
    ParsedDocument,
    ParsedImage,
    ParsedSection,
    ParsedTable,
)

if TYPE_CHECKING:
    from pathlib import Path

    from docling.document_converter import DocumentConverter

logger = logging.getLogger(__name__)

_SHARED_CONVERTER: DocumentConverter | None = None


def _get_converter() -> DocumentConverter:
    """Lazily instantiate and cache the Docling DocumentConverter."""
    global _SHARED_CONVERTER
    if _SHARED_CONVERTER is None:
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                RapidOcrOptions,
            )
            from docling.document_converter import (
                DocumentConverter,
                PdfFormatOption,
            )

            from veydus.config import settings

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True
            if settings.docling_ocr_engine.lower() == "rapidocr":
                pipeline_options.ocr_options = RapidOcrOptions()

            format_options: dict[Any, Any] = {
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
            _SHARED_CONVERTER = DocumentConverter(format_options=format_options)
        except Exception as e:
            logger.warning(
                "Failed to configure custom Docling pipeline options, using default: %s", e
            )
            from docling.document_converter import DocumentConverter

            _SHARED_CONVERTER = DocumentConverter()
    return _SHARED_CONVERTER


class DoclingParser:
    """Document parser leveraging Docling for PDF, DOCX, PPTX, and image formats."""

    async def parse(self, file_path: Path, mime_type: str | None = None) -> ParsedDocument:
        """Parse document using Docling in a separate thread."""
        return await asyncio.to_thread(self._sync_parse, file_path)

    def _sync_parse(self, file_path: Path) -> ParsedDocument:
        """Synchronously execute Docling conversion and extract structured elements."""
        converter = _get_converter()
        logger.info("Parsing file with Docling: %s", file_path)
        conv_result = converter.convert(str(file_path))
        doc = conv_result.document

        title = file_path.stem
        raw_markdown = ""
        try:
            raw_markdown = doc.export_to_markdown()
        except Exception as e:
            logger.warning("Failed to export Docling document to markdown: %s", e)

        sections: list[ParsedSection] = []
        tables: list[ParsedTable] = []
        images: list[ParsedImage] = []

        # Extract structured tables
        try:
            for table_idx, table in enumerate(getattr(doc, "tables", [])):
                table_md = ""
                rows: list[list[str]] = []
                page_no: int | None = None

                if hasattr(table, "export_to_markdown"):
                    table_md = table.export_to_markdown()
                elif hasattr(table, "text"):
                    table_md = str(table.text)

                if hasattr(table, "prov") and table.prov:
                    page_no = getattr(table.prov[0], "page_no", None)

                if hasattr(table, "data") and hasattr(table.data, "grid"):
                    for row in table.data.grid:
                        rows.append([str(getattr(cell, "text", cell)) for cell in row])

                tables.append(
                    ParsedTable(
                        caption=f"Table {table_idx + 1}",
                        markdown=table_md,
                        rows=rows,
                        page_number=page_no,
                    )
                )
        except Exception as e:
            logger.warning("Error extracting tables from Docling document: %s", e)

        # Extract structured sections by inspecting document items or falling back to markdown
        try:
            from docling_core.types.doc.items.text import SectionHeaderItem, TextItem

            current_section_title = file_path.stem
            current_level = 1
            current_content_parts: list[str] = []
            current_page_no: int | None = 1

            for item, _level in doc.iterate_items():
                page_no = None
                if hasattr(item, "prov") and item.prov:
                    page_no = getattr(item.prov[0], "page_no", None)

                if isinstance(item, SectionHeaderItem):
                    # Flush previous section
                    content_text = "\n".join(current_content_parts).strip()
                    if content_text:
                        sections.append(
                            ParsedSection(
                                title=current_section_title,
                                level=current_level,
                                content=content_text,
                                page_number=current_page_no,
                            )
                        )
                    current_section_title = (
                        getattr(item, "text", "").strip() or current_section_title
                    )
                    current_level = getattr(item, "level", 1)
                    current_page_no = page_no or current_page_no
                    current_content_parts = []
                elif isinstance(item, TextItem):
                    text_val = getattr(item, "text", "").strip()
                    if text_val:
                        current_content_parts.append(text_val)

            # Flush final section
            content_text = "\n".join(current_content_parts).strip()
            if content_text or not sections:
                sections.append(
                    ParsedSection(
                        title=current_section_title,
                        level=current_level,
                        content=content_text,
                        page_number=current_page_no,
                    )
                )
        except Exception as e:
            logger.warning(
                "Error extracting sections via iterate_items, falling back to markdown: %s", e
            )
            # Fallback to TextAndMarkdownParser logic on the raw markdown
            from veydus.ingestion.parsers.text import TextAndMarkdownParser

            text_parser = TextAndMarkdownParser()
            md_doc = text_parser._parse_markdown(file_path.name, raw_markdown)
            sections = md_doc.sections
            if not tables:
                tables = md_doc.tables

        return ParsedDocument(
            title=title,
            sections=sections,
            tables=tables,
            images=images,
            raw_text=raw_markdown,
            metadata={
                "filename": file_path.name,
                "format": file_path.suffix.lstrip("."),
                "num_sections": len(sections),
                "num_tables": len(tables),
            },
        )
