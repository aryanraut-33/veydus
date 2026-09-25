# ─────────────────────────────────────────────────────────────────
# VEYDUS — Transient Staging Storage
# ─────────────────────────────────────────────────────────────────
# What:  Transient storage abstraction and local filesystem implementation
#        for staging uploaded documents during ingestion.
# How:   Stages files to a designated directory ({staging_dir}/{source_id}/{filename}),
#        provides an async context manager ensuring guaranteed cleanup on completion
#        or error, and defines an interface for future object storage backends.
# Why:   HLD §7.2 & §7.5 require documents to be transiently staged for parsing
#        and immediately purged from disk once chunks are embedded and indexed.
# Tools: pathlib, shutil, asyncio, contextlib.asynccontextmanager, Pydantic/Settings.
# ─────────────────────────────────────────────────────────────────
from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from veydus.config import Settings
from veydus.config import settings as default_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager
    from uuid import UUID

logger = logging.getLogger(__name__)


@runtime_checkable
class StagingStorage(Protocol):
    """Protocol for transient document staging storage."""

    async def stage(self, source_id: UUID, filename: str, content: bytes) -> Path:
        """Write content to transient staging storage and return the local Path."""
        ...

    async def cleanup(self, path: Path) -> None:
        """Purge staged file and associated temporary directory."""
        ...

    def stage_context(
        self, source_id: UUID, filename: str, content: bytes
    ) -> AbstractAsyncContextManager[Path]:
        """Context manager yielding the staged path with guaranteed cleanup on exit."""
        ...


class LocalStorage:
    """Local filesystem staging storage using configured staging directory."""

    def __init__(
        self, base_dir: Path | str | None = None, settings: Settings | None = None
    ) -> None:
        cfg = settings or default_settings
        self.base_dir = Path(base_dir or cfg.staging_local_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def stage(self, source_id: UUID, filename: str, content: bytes) -> Path:
        """Write content to local staging directory in a threadpool."""
        target_dir = self.base_dir / str(source_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / filename

        def _write() -> None:
            file_path.write_bytes(content)

        await asyncio.to_thread(_write)
        logger.debug("Staged %d bytes for source %s at %s", len(content), source_id, file_path)
        return file_path

    async def cleanup(self, path: Path) -> None:
        """Safely delete staged file and prune empty parent directory."""

        def _delete() -> None:
            try:
                if path.exists():
                    path.unlink()
                    logger.debug("Deleted staged file %s", path)
                parent = path.parent
                if parent.exists() and not any(parent.iterdir()):
                    shutil.rmtree(parent, ignore_errors=True)
                    logger.debug("Pruned empty staging directory %s", parent)
            except Exception as e:
                logger.warning("Error during staging cleanup for %s: %s", path, e)

        await asyncio.to_thread(_delete)

    @asynccontextmanager
    async def stage_context(
        self, source_id: UUID, filename: str, content: bytes
    ) -> AsyncIterator[Path]:
        """Context manager guaranteeing cleanup of staged file even upon errors."""
        path = await self.stage(source_id, filename, content)
        try:
            yield path
        finally:
            await self.cleanup(path)


def get_staging_storage(settings: Settings | None = None) -> StagingStorage:
    """Factory to retrieve configured staging storage."""
    cfg = settings or default_settings
    backend = cfg.staging_backend.lower().strip()
    if backend == "local":
        return LocalStorage(settings=cfg)
    else:
        # S3 / cloud storage fallback
        logger.warning("Unsupported staging backend '%s', falling back to LocalStorage", backend)
        return LocalStorage(settings=cfg)
