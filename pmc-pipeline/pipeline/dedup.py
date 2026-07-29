"""Content-hash deduplication layer (SHA-256).

Provides the fourth dedup tier (after PMID / PMCID / DOI uniqueness
enforced at the database level).  Two papers with different PMIDs can
still contain identical XML — this module catches that case.
"""

from __future__ import annotations

import hashlib
import logging

from pipeline.database import PaperDatabase

logger = logging.getLogger("pmc_pipeline")


class ContentDeduplicator:
    """SHA-256 content-hash deduplication backed by SQLite."""

    def __init__(self, db: PaperDatabase) -> None:
        self.db = db

    @staticmethod
    def compute_hash(content: str | bytes) -> str:
        """Return the hex-encoded SHA-256 digest of *content*."""
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    async def is_duplicate(self, content: str | bytes) -> bool:
        """Check whether *content*'s hash is already registered."""
        h = self.compute_hash(content)
        return await self.db.content_hash_exists(h)

    async def register(self, pmcid: str, content: str | bytes) -> bool:
        """Register the content hash for *pmcid*.

        Returns ``True`` if the hash was new (paper is unique),
        ``False`` if a duplicate hash already existed.
        """
        h = self.compute_hash(content)
        is_new = await self.db.set_content_hash(pmcid, h)
        if not is_new:
            logger.info("Content duplicate detected for %s (hash %s…)", pmcid, h[:12])
        return is_new
