"""Async SQLite database for paper metadata and download tracking.

Uses ``aiosqlite`` with WAL journalling for safe concurrent reads/writes
from the async download workers.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiosqlite

from pipeline.parser import classify_evidence_category

logger = logging.getLogger("pmc_pipeline")

# ───────────────────────────────────────────────────────────────────────
# Schema SQL
# ───────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS papers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pmid            TEXT UNIQUE,
    pmcid           TEXT UNIQUE,
    doi             TEXT,
    title           TEXT,
    authors         TEXT,          -- JSON array serialised as string
    journal         TEXT,
    publication_date TEXT,
    article_type    TEXT,
    license         TEXT,
    status          TEXT DEFAULT 'DISCOVERED'
                    CHECK(status IN ('DISCOVERED','QUEUED_FOR_DOWNLOAD','DOWNLOADING','DOWNLOADED','PROCESSING','PROCESSED','EMBEDDING_PENDING','EMBEDDED','FAILED_DOWNLOAD','FAILED_PROCESSING','FAILED_EMBEDDING','excluded','duplicate')),
    download_url    TEXT,
    content_hash    TEXT,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    is_human_study  INTEGER DEFAULT 0,
    is_animal_study INTEGER DEFAULT 0,
    publication_types TEXT,        -- JSON array serialised as string
    mesh_terms      TEXT,          -- JSON array serialised as string
    clinical_trial_ids TEXT,       -- JSON array serialised as string
    evidence_category TEXT,        -- derived: guideline|meta_analysis|systematic_review|rct|other
    discovery_date  TEXT DEFAULT (datetime('now')),
    last_seen_date  TEXT DEFAULT (datetime('now')),
    last_modified_date TEXT DEFAULT (datetime('now')),
    downloaded_at   TEXT,
    processed_at    TEXT,
    embedded_at     TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date         TEXT DEFAULT (datetime('now')),
    query_used       TEXT,
    papers_found     INTEGER,
    papers_downloaded INTEGER,
    papers_failed    INTEGER,
    duration_seconds REAL
);

CREATE INDEX IF NOT EXISTS idx_papers_status       ON papers(status);
CREATE INDEX IF NOT EXISTS idx_papers_doi           ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_content_hash  ON papers(content_hash);
CREATE INDEX IF NOT EXISTS idx_papers_evidence_category ON papers(evidence_category);
"""


class PaperDatabase:
    """Async wrapper around ``papers.db``.

    Usage::

        db = PaperDatabase("data/metadata/papers.db")
        await db.connect()
        await db.initialize_schema()
        ...
        await db.close()

    Also usable as an async context manager::

        async with PaperDatabase(path) as db:
            ...
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    # -- lifecycle --------------------------------------------------------

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "PaperDatabase":
        await self.connect()
        await self.initialize_schema()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def initialize_schema(self) -> None:
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    # -- inserts ----------------------------------------------------------

    async def insert_paper(
        self,
        pmid: str,
        pmcid: str,
        doi: str | None = None,
        title: str | None = None,
        article_type: str | None = None,
        download_url: str | None = None,
        license: str | None = None,
        is_human_study: bool = False,
        is_animal_study: bool = False,
        publication_types: list[str] | None = None,
        mesh_terms: list[str] | None = None,
    ) -> bool:
        """Insert a paper.  Returns ``True`` if the row was actually new."""
        # Skip if DOI already present (cross-PMID dedup)
        if doi and await self.doi_exists(doi):
            return False
        import json
        pub_types_str = json.dumps(publication_types or [])
        mesh_terms_str = json.dumps(mesh_terms or [])
        evidence_cat = classify_evidence_category(publication_types or [])
        try:
            await self._db.execute(
                """INSERT OR IGNORE INTO papers
                       (pmid, pmcid, doi, title, article_type, download_url, license, is_human_study, is_animal_study, publication_types, mesh_terms, evidence_category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pmid,
                    pmcid,
                    doi,
                    title,
                    article_type,
                    download_url,
                    license,
                    int(is_human_study),
                    int(is_animal_study),
                    pub_types_str,
                    mesh_terms_str,
                    evidence_cat,
                ),
            )
            await self._db.commit()
            return self._db.total_changes > 0
        except Exception:
            # UNIQUE constraint on pmcid might fire if pmid was unique but
            # pmcid collides (shouldn't happen, but be safe).
            return False

    async def insert_papers_batch(self, papers: list[dict]) -> int:
        """Batch-insert papers.  Returns count of *new* rows inserted."""
        if not papers:
            return 0

        before = await self._total_rows()
        # Deduplicate by DOI within the batch itself
        seen_dois: set[str] = set()
        deduped: list[dict] = []
        for p in papers:
            doi = p.get("doi")
            if doi:
                if doi in seen_dois:
                    continue
                if await self.doi_exists(doi):
                    continue
                seen_dois.add(doi)
            deduped.append(p)

        import json
        await self._db.executemany(
            """INSERT INTO papers
                   (pmid, pmcid, doi, title, article_type, download_url, license, is_human_study, is_animal_study, publication_types, mesh_terms, evidence_category, status)
               VALUES (:pmid, :pmcid, :doi, :title, :article_type,
                       :download_url, :license, :is_human_study, :is_animal_study, :publication_types, :mesh_terms, :evidence_category, 'DISCOVERED')
               ON CONFLICT(pmid) DO UPDATE SET
                   last_seen_date = datetime('now'),
                   last_modified_date = CASE WHEN (doi != excluded.doi OR download_url != excluded.download_url) THEN datetime('now') ELSE last_modified_date END,
                   download_url = COALESCE(excluded.download_url, download_url),
                   license = COALESCE(excluded.license, license)""",
            [
                {
                    "pmid": p.get("pmid"),
                    "pmcid": p.get("pmcid"),
                    "doi": p.get("doi"),
                    "title": p.get("title"),
                    "article_type": p.get("article_type"),
                    "download_url": p.get("download_url"),
                    "license": p.get("license"),
                    "is_human_study": int(p.get("is_human_study", False)),
                    "is_animal_study": int(p.get("is_animal_study", False)),
                    "publication_types": json.dumps(p.get("publication_types") or []),
                    "mesh_terms": json.dumps(p.get("mesh_terms") or []),
                    "evidence_category": classify_evidence_category(p.get("publication_types") or []),
                }
                for p in deduped
            ],
        )
        await self._db.commit()
        after = await self._total_rows()
        return after - before

    # -- status management ------------------------------------------------

    async def update_status(
        self, pmcid: str, status: str, error_message: str | None = None
    ) -> None:
        await self._db.execute(
            """UPDATE papers
               SET status = ?, error_message = ?, updated_at = datetime('now')
               WHERE pmcid = ?""",
            (status, error_message, pmcid),
        )
        await self._db.commit()

    async def update_paper_metadata(self, pmcid: str, **kwargs: Any) -> None:
        """Update arbitrary columns for a paper identified by *pmcid*."""
        if not kwargs:
            return
        cols = ", ".join(f"{k} = ?" for k in kwargs)
        vals = list(kwargs.values()) + [pmcid]
        await self._db.execute(
            f"UPDATE papers SET {cols}, updated_at = datetime('now') WHERE pmcid = ?",
            vals,
        )
        await self._db.commit()

    async def set_content_hash(self, pmcid: str, content_hash: str) -> bool:
        """Set the content hash.  Returns ``False`` if the hash already exists
        (i.e. content-level duplicate)."""
        if await self.content_hash_exists(content_hash):
            return False
        await self._db.execute(
            "UPDATE papers SET content_hash = ? WHERE pmcid = ?",
            (content_hash, pmcid),
        )
        await self._db.commit()
        return True

    async def reset_downloading_to_pending(self) -> int:
        """On restart, unstick papers left in *downloading* state."""
        cursor = await self._db.execute(
            "UPDATE papers SET status = 'QUEUED_FOR_DOWNLOAD' WHERE status = 'DOWNLOADING' OR status = 'PROCESSING'"
        )
        await self._db.commit()
        return cursor.rowcount

    async def increment_retry_count(self, pmcid: str) -> None:
        await self._db.execute(
            "UPDATE papers SET retry_count = retry_count + 1 WHERE pmcid = ?",
            (pmcid,),
        )
        await self._db.commit()

    # -- queries ----------------------------------------------------------

    async def get_pending_papers(
        self, limit: int | None = None, max_retries: int = 5
    ) -> list[dict]:
        """Papers that still need downloading."""
        sql = """SELECT pmid, pmcid, doi, download_url, retry_count, is_human_study, is_animal_study, publication_types, mesh_terms, clinical_trial_ids
                 FROM papers
                 WHERE (status IN ('QUEUED_FOR_DOWNLOAD', 'DOWNLOADING', 'PROCESSING')
                        OR (status IN ('FAILED_DOWNLOAD', 'FAILED_PROCESSING', 'FAILED_EMBEDDING') AND retry_count < ?))
                 ORDER BY created_at"""
        params: list = [max_retries]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        
        result_list = []
        for r in rows:
            d = dict(r)
            d["publication_types"] = json.loads(d["publication_types"]) if d.get("publication_types") else []
            d["mesh_terms"] = json.loads(d["mesh_terms"]) if d.get("mesh_terms") else []
            d["clinical_trial_ids"] = json.loads(d["clinical_trial_ids"]) if d.get("clinical_trial_ids") else []
            result_list.append(d)
        return result_list

    async def get_discovered_pool(
        self,
        limit: int | None = None,
        sort_order: str = "newest",
        pub_start_year: int | None = None,
        pub_end_year: int | None = None,
    ) -> list[dict]:
        """Select papers with status='DISCOVERED' using criteria."""
        conditions = ["status = 'DISCOVERED'"]
        params = []

        if pub_start_year:
            conditions.append("publication_date >= ?")
            params.append(f"{pub_start_year}-01-01")
        if pub_end_year:
            conditions.append("publication_date <= ?")
            params.append(f"{pub_end_year}-12-31")

        sql = f"SELECT pmcid, pmid, title, publication_date FROM papers WHERE {' AND '.join(conditions)}"

        # Sorting
        if sort_order == "newest":
            sql += " ORDER BY publication_date DESC, pmid DESC"
        elif sort_order == "oldest":
            sql += " ORDER BY publication_date ASC, pmid ASC"
        elif sort_order == "random":
            sql += " ORDER BY RANDOM()"
        else:
            # Fallback to newest
            sql += " ORDER BY publication_date DESC, pmid DESC"

        if limit:
            sql += " LIMIT ?"
            params.append(limit)

        cursor = await self._db.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def mark_queued_for_download(self, pmcids: list[str]) -> None:
        """Lock discovered papers for download by changing status to 'QUEUED_FOR_DOWNLOAD'."""
        if not pmcids:
            return
        # Split into chunks of 900 because SQLite parameter limit is 999
        chunk_size = 900
        for i in range(0, len(pmcids), chunk_size):
            chunk = pmcids[i : i + chunk_size]
            placeholders = ", ".join("?" for _ in chunk)
            await self._db.execute(
                f"UPDATE papers SET status = 'QUEUED_FOR_DOWNLOAD', updated_at = datetime('now') WHERE pmcid IN ({placeholders}) AND status = 'DISCOVERED'",
                chunk,
            )
        await self._db.commit()

    async def get_paper_count_by_status(self) -> dict[str, int]:
        cursor = await self._db.execute(
            "SELECT status, COUNT(*) as cnt FROM papers GROUP BY status"
        )
        rows = await cursor.fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    async def pmid_exists(self, pmid: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM papers WHERE pmid = ?", (pmid,)
        )
        return (await cursor.fetchone()) is not None

    async def pmcid_exists(self, pmcid: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM papers WHERE pmcid = ?", (pmcid,)
        )
        return (await cursor.fetchone()) is not None

    async def doi_exists(self, doi: str) -> bool:
        if not doi:
            return False
        cursor = await self._db.execute(
            "SELECT 1 FROM papers WHERE doi = ?", (doi,)
        )
        return (await cursor.fetchone()) is not None

    async def content_hash_exists(self, hash_val: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM papers WHERE content_hash = ?", (hash_val,)
        )
        return (await cursor.fetchone()) is not None

    async def get_all_pmids(self) -> set[str]:
        cursor = await self._db.execute("SELECT pmid FROM papers")
        rows = await cursor.fetchall()
        return {r["pmid"] for r in rows}

    async def get_stats(self) -> dict[str, Any]:
        status_counts = await self.get_paper_count_by_status()
        total = sum(status_counts.values())
        return {"total": total, **status_counts}

    # -- run history ------------------------------------------------------

    async def record_run(
        self,
        query: str,
        found: int,
        downloaded: int,
        failed: int,
        duration: float,
    ) -> None:
        await self._db.execute(
            """INSERT INTO run_history
                   (query_used, papers_found, papers_downloaded,
                    papers_failed, duration_seconds)
               VALUES (?, ?, ?, ?, ?)""",
            (query, found, downloaded, failed, duration),
        )
        await self._db.commit()

    # -- private helpers --------------------------------------------------

    async def _total_rows(self) -> int:
        cursor = await self._db.execute("SELECT COUNT(*) FROM papers")
        row = await cursor.fetchone()
        return row[0]
