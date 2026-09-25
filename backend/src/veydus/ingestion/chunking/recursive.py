# ─────────────────────────────────────────────────────────────────
# VEYDUS — Recursive Structure-Aware Chunker
# ─────────────────────────────────────────────────────────────────
# What:  Chunker that respects document structure (sections, headings, tables)
#        and uses recursive text splitting (paragraphs, sentences, words)
#        with sliding window overlap.
# How:   Traverses ParsedDocument sections and tables, applies recursive boundary
#        splitting, enforces min/max size constraints, and attaches section headings
#        and page numbers to each ChunkItem.
# Why:   HLD §7.2 requires chunking that preserves context boundaries and avoids
#        splitting tables across chunks. Tunable parameters flow from Settings
#        (target_size=512, overlap=64, min_size=64, max_size=1024).
# Tools: re, typing, Pydantic v2, veydus.config.Settings.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import re
from typing import Any

from veydus.config import Settings
from veydus.config import settings as default_settings
from veydus.ingestion.chunking.base import ChunkItem
from veydus.ingestion.parsers.base import ParsedDocument, ParsedSection

SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.?!])\s+")


class RecursiveStructureChunker:
    """Structure-aware recursive chunker respecting section boundaries, headings, and tables."""

    def __init__(
        self,
        target_size: int | None = None,
        overlap: int | None = None,
        min_size: int | None = None,
        max_size: int | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or default_settings
        self.target_size = target_size if target_size is not None else cfg.chunk_target_size
        self.overlap = overlap if overlap is not None else cfg.chunk_overlap
        self.min_size = min_size if min_size is not None else cfg.chunk_min_size
        self.max_size = max_size if max_size is not None else cfg.chunk_max_size

    def chunk(self, doc: ParsedDocument) -> list[ChunkItem]:
        """Transform ParsedDocument into an ordered list of ChunkItems."""
        chunks: list[ChunkItem] = []
        chunk_idx = 0

        # 1. Process structured sections
        for section in doc.sections:
            section_chunks = self._chunk_section(section, start_index=chunk_idx)
            chunks.extend(section_chunks)
            chunk_idx += len(section_chunks)

        # 2. Process standalone tables (if any)
        for table in doc.tables:
            # Check if table markdown is already present in raw section text to avoid duplication
            table_md = table.markdown.strip()
            if not table_md:
                continue

            already_in_chunks = any(table_md in c.content for c in chunks)
            if not already_in_chunks:
                table_chunks = self._chunk_table(
                    table_md, table.caption, table.page_number, start_index=chunk_idx
                )
                chunks.extend(table_chunks)
                chunk_idx += len(table_chunks)

        # Fallback: if document had no sections, chunk raw_text
        if not chunks and doc.raw_text.strip():
            fallback_section = ParsedSection(
                title=doc.title,
                level=1,
                content=doc.raw_text.strip(),
                page_number=1,
            )
            chunks = self._chunk_section(fallback_section, start_index=0)

        # Post-process: re-index sequential chunk indices to be strictly contiguous 0..N-1
        for idx, item in enumerate(chunks):
            item.chunk_index = idx

        return chunks

    def _chunk_section(self, section: ParsedSection, start_index: int) -> list[ChunkItem]:
        """Chunk a single section into bounded chunks with overlap."""
        text = section.content.strip()
        if not text:
            return []

        # If text fits within target_size, return as single chunk
        if len(text) <= self.target_size:
            return [
                ChunkItem(
                    content=text,
                    chunk_index=start_index,
                    section_heading=section.title or None,
                    page_number=section.page_number,
                    metadata={"section_level": section.level},
                )
            ]

        # Break text recursively: paragraphs -> sentences
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        units: list[str] = []
        for p in paragraphs:
            if len(p) <= self.target_size:
                units.append(p)
            else:
                sentences = [s.strip() for s in SENTENCE_SPLIT_PATTERN.split(p) if s.strip()]
                for s in sentences:
                    if len(s) <= self.target_size:
                        units.append(s)
                    else:
                        # Break large sentence by words
                        words = s.split()
                        curr_words: list[str] = []
                        curr_len = 0
                        for w in words:
                            if curr_len + len(w) + 1 > self.target_size and curr_words:
                                units.append(" ".join(curr_words))
                                curr_words = [w]
                                curr_len = len(w)
                            else:
                                curr_words.append(w)
                                curr_len += len(w) + 1
                        if curr_words:
                            units.append(" ".join(curr_words))

        # Pack units into chunks respecting target_size and overlap
        chunks: list[ChunkItem] = []
        current_pieces: list[str] = []
        current_len = 0
        idx = start_index

        for unit in units:
            unit_len = len(unit)
            if current_len + unit_len + 2 > self.target_size and current_pieces:
                chunk_str = "\n\n".join(current_pieces).strip()
                chunks.append(
                    ChunkItem(
                        content=chunk_str,
                        chunk_index=idx,
                        section_heading=section.title or None,
                        page_number=section.page_number,
                        metadata={"section_level": section.level},
                    )
                )
                idx += 1

                # Calculate overlap: retain tail pieces that fit within self.overlap
                overlap_pieces: list[str] = []
                overlap_len = 0
                for piece in reversed(current_pieces):
                    if overlap_len + len(piece) <= self.overlap:
                        overlap_pieces.insert(0, piece)
                        overlap_len += len(piece)
                    else:
                        break

                current_pieces = overlap_pieces + [unit]
                current_len = sum(len(p) for p in current_pieces) + (len(current_pieces) - 1) * 2
            else:
                current_pieces.append(unit)
                current_len += unit_len + 2

        if current_pieces:
            chunk_str = "\n\n".join(current_pieces).strip()
            # If the last chunk is very small (< min_size) and we already have chunks, append to previous if possible
            if len(chunk_str) < self.min_size and chunks:
                prev_chunk = chunks[-1]
                if len(prev_chunk.content) + len(chunk_str) + 2 <= self.max_size:
                    prev_chunk.content = f"{prev_chunk.content}\n\n{chunk_str}"
                else:
                    chunks.append(
                        ChunkItem(
                            content=chunk_str,
                            chunk_index=idx,
                            section_heading=section.title or None,
                            page_number=section.page_number,
                            metadata={"section_level": section.level},
                        )
                    )
            else:
                chunks.append(
                    ChunkItem(
                        content=chunk_str,
                        chunk_index=idx,
                        section_heading=section.title or None,
                        page_number=section.page_number,
                        metadata={"section_level": section.level},
                    )
                )

        return chunks

    def _chunk_table(
        self, table_md: str, caption: str | None, page_number: int | None, start_index: int
    ) -> list[ChunkItem]:
        """Wrap table markdown in a ChunkItem or chunk row-wise if exceeding max_size."""
        meta: dict[str, Any] = {"is_table": True}
        if caption:
            meta["caption"] = caption

        if len(table_md) <= self.max_size:
            return [
                ChunkItem(
                    content=table_md,
                    chunk_index=start_index,
                    section_heading=caption or "Table",
                    page_number=page_number,
                    metadata=meta,
                )
            ]

        # If table exceeds max_size, split by rows
        lines = table_md.splitlines()
        header_lines = lines[:2] if len(lines) >= 2 else []
        body_lines = lines[2:] if len(lines) >= 2 else lines

        chunks: list[ChunkItem] = []
        curr_rows: list[str] = []
        idx = start_index

        for row in body_lines:
            candidate = "\n".join(header_lines + curr_rows + [row])
            if len(candidate) > self.max_size and curr_rows:
                chunk_str = "\n".join(header_lines + curr_rows)
                chunks.append(
                    ChunkItem(
                        content=chunk_str,
                        chunk_index=idx,
                        section_heading=caption or "Table",
                        page_number=page_number,
                        metadata=meta,
                    )
                )
                idx += 1
                curr_rows = [row]
            else:
                curr_rows.append(row)

        if curr_rows:
            chunk_str = "\n".join(header_lines + curr_rows)
            chunks.append(
                ChunkItem(
                    content=chunk_str,
                    chunk_index=idx,
                    section_heading=caption or "Table",
                    page_number=page_number,
                    metadata=meta,
                )
            )

        return chunks
