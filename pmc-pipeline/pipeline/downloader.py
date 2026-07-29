"""Async download engine for PubMed Central full-text papers.

Implements a producer-consumer architecture using :mod:`asyncio` queues
and a bounded semaphore so that at most ``max_workers`` papers are
downloaded concurrently.  Each paper goes through:

1. **Download** — OA FTP/HTTPS archive first, efetch XML fallback.
2. **Deduplication** — content-hash checked against the database.
3. **Parse** — JATS XML → structured :class:`~pipeline.parser.ParsedPaper`.
4. **Persist** — raw XML saved to disk, parsed JSON saved to disk,
   metadata + status written back to the database.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import io
import json
import logging
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING

import aiofiles
import aiohttp

from pipeline.config import AppConfig
from pipeline.database import PaperDatabase
from pipeline.dedup import ContentDeduplicator
from pipeline.parser import JATSParser
from pipeline.chunker import SectionChunker
from pipeline.manifest_validator import validate_manifest_item
from pipeline.vector_index import QdrantIndexClient
from pipeline.utils import (
    ThroughputTracker,
    TokenBucketRateLimiter,
    retry_with_backoff,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("pmc_pipeline")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATS_LOG_INTERVAL = 100  # log throughput every N papers


class PMCDownloader:
    """Downloads, parses, deduplicates, and persists PMC full-text papers.

    The download flow tries two sources in order:

    1. **OA archive** — ``download_url`` points to a ``.tar.gz`` on the PMC
       OA FTP mirror (often served via HTTPS).  The archive contains the
       JATS ``.nxml`` file.
    2. **E-utilities efetch** — direct XML retrieval for papers that have
       no OA archive URL.

    Attributes
    ----------
    EFETCH_URL : str
        Base URL for NCBI E-utilities efetch endpoint.
    config : AppConfig
        Application configuration.
    db : PaperDatabase
        Async paper database handle.
    rate_limiter : TokenBucketRateLimiter
        Token bucket for NCBI API rate compliance.
    session : aiohttp.ClientSession
        Shared HTTP session.
    tracker : ThroughputTracker
        Records success / failure / retry counts.
    parser : JATSParser
        JATS XML → structured data parser.
    dedup : ContentDeduplicator
        Content-hash based deduplication against the database.
    semaphore : asyncio.Semaphore
        Bounds concurrent download workers.
    """

    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def __init__(
        self,
        config: AppConfig,
        db: PaperDatabase,
        rate_limiter: TokenBucketRateLimiter,
        session: aiohttp.ClientSession,
        tracker: ThroughputTracker,
    ) -> None:
        self.config = config
        self.db = db
        self.rate_limiter = rate_limiter
        self.session = session
        self.tracker = tracker
        self.parser = JATSParser(config)
        self.dedup = ContentDeduplicator(db)
        self.semaphore = asyncio.Semaphore(config.pipeline.max_workers)
        self._processed_count: int = 0
        self.chunker = SectionChunker()
        self.qdrant = QdrantIndexClient()

    # ------------------------------------------------------------------
    # Download helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_nxml_from_tar(data: bytes) -> bytes | None:
        """Extract the ``.nxml`` file from an in-memory tar.gz archive.

        Parameters
        ----------
        data:
            Raw bytes of the ``.tar.gz`` archive.

        Returns
        -------
        bytes or None
            Content of the first ``.nxml`` member, or *None* if no such
            member exists or the archive is corrupt.
        """
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith(".nxml") and member.isfile():
                        extracted = tar.extractfile(member)
                        if extracted is not None:
                            return extracted.read()
        except (tarfile.ReadError, tarfile.CompressionError, EOFError) as exc:
            logger.warning("Corrupt tar.gz archive: %s", exc)
        return None

    async def download_oa_paper(
        self, pmcid: str, download_url: str
    ) -> bytes | None:
        """Download a ``.tar.gz`` from the OA FTP/HTTPS mirror and extract XML.

        If *download_url* uses the ``ftp://`` scheme it is transparently
        converted to ``https://`` (PMC serves the same archives over both
        protocols).

        Parameters
        ----------
        pmcid:
            PubMed Central identifier (e.g. ``PMC1234567``).
        download_url:
            Full URL to the OA archive.

        Returns
        -------
        bytes or None
            Raw JATS XML bytes, or *None* on failure.
        """
        # Normalise FTP → HTTPS
        if download_url.startswith("ftp://"):
            download_url = download_url.replace("ftp://", "https://", 1)

        logger.debug("OA download %s from %s", pmcid, download_url)
        await self.rate_limiter.acquire()

        try:
            download_timeout = aiohttp.ClientTimeout(
                total=self.config.pipeline.download_timeout,
            )
            async with self.session.get(
                download_url, timeout=download_timeout
            ) as resp:
                resp.raise_for_status()
                archive_bytes = await resp.read()
        except aiohttp.ClientError as exc:
            logger.warning(
                "OA download failed for %s (%s): %s",
                pmcid,
                download_url,
                exc,
            )
            return None

        xml_bytes = self._extract_nxml_from_tar(archive_bytes)
        if xml_bytes is None:
            logger.warning(
                "No .nxml found in archive for %s (%s)", pmcid, download_url
            )
        return xml_bytes

    async def download_efetch_paper(self, pmcid: str) -> bytes | None:
        """Download full-text XML for *pmcid* via E-utilities efetch.

        Parameters
        ----------
        pmcid:
            PubMed Central identifier (e.g. ``PMC1234567``).

        Returns
        -------
        bytes or None
            Raw JATS XML bytes, or *None* on failure.
        """
        params: dict[str, str] = {
            "db": "pmc",
            "id": pmcid,
            "rettype": "full",
            "retmode": "xml",
        }
        ncbi = self.config.ncbi
        if ncbi.api_key:
            params["api_key"] = ncbi.api_key
        if ncbi.tool_name:
            params["tool"] = ncbi.tool_name
        if ncbi.email:
            params["email"] = ncbi.email

        logger.debug("efetch download %s", pmcid)
        await self.rate_limiter.acquire()

        try:
            download_timeout = aiohttp.ClientTimeout(
                total=self.config.pipeline.download_timeout,
            )
            async with self.session.get(
                self.EFETCH_URL, params=params, timeout=download_timeout
            ) as resp:
                resp.raise_for_status()
                return await resp.read()
        except aiohttp.ClientError as exc:
            logger.warning("efetch download failed for %s: %s", pmcid, exc)
            return None

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _save_bytes(path: Path, data: bytes) -> None:
        """Write raw *data* to *path* asynchronously."""
        async with aiofiles.open(path, "wb") as fh:
            await fh.write(data)

    @staticmethod
    async def _save_json(path: Path, data: dict) -> None:
        """Serialise *data* as pretty-printed JSON and write to *path*."""
        payload = json.dumps(data, indent=2, ensure_ascii=False)
        async with aiofiles.open(path, "w", encoding="utf-8") as fh:
            await fh.write(payload)

    # ------------------------------------------------------------------
    # Core processing
    # ------------------------------------------------------------------

    async def process_paper(self, paper: dict) -> None:
        """Download, parse, deduplicate, and save a single paper.

        This is the main per-paper workflow orchestrating every step from
        download to database update.

        Parameters
        ----------
        paper:
            A dict as returned by :meth:`PaperDatabase.get_pending_papers`,
            expected to contain at least ``pmcid`` and optionally
            ``download_url``.

        The method is intentionally *not* decorated with ``retry_with_backoff``
        because the individual download calls already implement retries.
        Errors that escape those retries are recorded as *failed* in the
        database so they can be retried on the next pipeline run.
        """
        pmcid: str = paper["pmcid"]
        download_url: str | None = paper.get("download_url")

        # Mark as downloading ------------------------------------------------
        phase = "download"

        try:
            await self.db.update_status(pmcid, "DOWNLOADING")

            # 2. Download XML ---------------------------------------------------
            xml_bytes: bytes | None = None

            if download_url:
                xml_bytes = await self.download_oa_paper(pmcid, download_url)

            if xml_bytes is None:
                xml_bytes = await self.download_efetch_paper(pmcid)

            if xml_bytes is None:
                raise RuntimeError(f"All download methods exhausted for {pmcid}")

            # DOWNLOADED
            await self.db.update_status(pmcid, "DOWNLOADED")
            await self.db.update_paper_metadata(
                pmcid, downloaded_at=datetime.now().isoformat()
            )

            # 3. Content-hash deduplication -------------------------------------
            content_hash = self.dedup.compute_hash(xml_bytes)
            if await self.dedup.is_duplicate(xml_bytes):
                logger.info("Duplicate content detected for %s — skipping", pmcid)
                await self.db.set_content_hash(pmcid, content_hash)
                await self.db.update_status(pmcid, "duplicate")
                self.tracker.record_success()
                return

            # 4. Save raw XML ---------------------------------------------------
            xml_path = Path(self.config.paths.xml_dir) / f"{pmcid}.xml"
            await self._save_bytes(xml_path, xml_bytes)

            # PROCESSING
            phase = "processing"
            await self.db.update_status(pmcid, "PROCESSING")

            # 5. Parse XML → structured data ------------------------------------
            parsed = self.parser.parse(xml_bytes, pmcid)
            parsed.is_human_study = bool(paper.get("is_human_study", False))
            parsed.is_animal_study = bool(paper.get("is_animal_study", False))

            # Merge MeSH terms and Publication Types from database (efetch) with JATS XML fallbacks
            db_pub_types = paper.get("publication_types") or []
            parsed.publication_types = list(dict.fromkeys(db_pub_types + parsed.publication_types))

            db_mesh_terms = paper.get("mesh_terms") or []
            parsed.mesh_terms = list(dict.fromkeys(db_mesh_terms + parsed.mesh_terms))

            # 6. Check exclusion (retracted, editorial, etc.) -------------------
            if parsed.is_excluded:
                logger.info(
                    "Paper %s excluded: %s", pmcid, parsed.exclusion_reason
                )
                await self.db.update_status(
                    pmcid,
                    "excluded",
                    error_message=parsed.exclusion_reason,
                )
                self.tracker.record_success()
                return

            # 7. Build and Validate Ingestion Manifest Item ---------------------
            json_data = self.parser.to_json(parsed)
            manifest_item = self._build_manifest_item(parsed, xml_bytes, xml_path)
            
            # Run schema validation gate
            validate_manifest_item(manifest_item)
            
            # Save validated JSON manifest item structure
            json_path = Path(self.config.paths.json_dir) / f"{pmcid}.json"
            await self._save_json(json_path, manifest_item)

            # 8. Register content hash and update database ----------------------
            await self.dedup.register(pmcid, xml_bytes)

            metadata_updates: dict = {}
            if parsed.title:
                metadata_updates["title"] = parsed.title
            if parsed.article_type:
                metadata_updates["article_type"] = parsed.article_type
            if parsed.doi:
                metadata_updates["doi"] = parsed.doi
            
            metadata_updates["publication_types"] = json.dumps(parsed.publication_types)
            metadata_updates["mesh_terms"] = json.dumps(parsed.mesh_terms)
            metadata_updates["clinical_trial_ids"] = json.dumps(parsed.clinical_trial_ids)
            metadata_updates["processed_at"] = datetime.now().isoformat()

            if metadata_updates:
                await self.db.update_paper_metadata(pmcid, **metadata_updates)

            # PROCESSED
            await self.db.update_status(pmcid, "PROCESSED")

            # 9. Chunking and Vector Indexing (Qdrant) --------------------------
            phase = "embedding"
            await self.db.update_status(pmcid, "EMBEDDING_PENDING")

            # Perform section-aware chunking
            qpoints = []
            import hashlib
            import uuid
            
            for section in manifest_item["sections"]:
                section_kind = section["section_kind"]
                section_text = section["text"]
                
                # Chunk this section
                evidence_units = self.chunker.chunk_section(section_text, f"{pmcid}_{section_kind}")
                
                for eu in evidence_units:
                    for chunk in eu.child_chunks:
                        # Generate unique point ID (UUID v5 based on chunk ID)
                        point_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk.id))
                        
                        # Generate mock dense BGE-M3 embedding (1024 floats)
                        dense_vector = [float(i % 10) / 10.0 for i in range(1024)]
                        
                        # Generate mock sparse BGE-M3 representation
                        sparse_vector = {
                            "indices": [10, 25, 42],
                            "values": [0.6, 0.35, 0.15]
                        }
                        
                        # Build filter payload
                        payload = {
                            "chunk_id": chunk.id,
                            "evidence_unit_id": eu.id,
                            "document_id": pmcid,
                            "study_type": manifest_item["study_type"],
                            "mesh_terms": manifest_item["mesh_terms"],
                            "retraction_status": manifest_item["retraction_status"],
                            "display_rights": manifest_item["display_rights"]
                        }
                        
                        qpoints.append({
                            "point_id": point_uuid,
                            "dense_vector": dense_vector,
                            "sparse_vector": sparse_vector,
                            "payload": payload
                        })
            
            # Process tables for vector indexing
            for table_idx, table in enumerate(manifest_item["tables"]):
                linearized_text = table["linearized_text"]
                if not linearized_text.strip():
                    continue
                
                # Treat each table as a single parent Evidence Unit and Child Chunk
                eu_id = f"{pmcid}_table_{table_idx + 1:02d}_P01"
                chunk_id = f"{eu_id}_C01"
                point_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))
                
                # Generate mock dense BGE-M3 embedding (1024 floats)
                dense_vector = [float(i % 10) / 10.0 for i in range(1024)]
                
                # Generate mock sparse BGE-M3 representation
                sparse_vector = {
                    "indices": [10, 25, 42],
                    "values": [0.6, 0.35, 0.15]
                }
                
                # Build filter payload
                payload = {
                    "chunk_id": chunk_id,
                    "evidence_unit_id": eu_id,
                    "document_id": pmcid,
                    "study_type": manifest_item["study_type"],
                    "mesh_terms": manifest_item["mesh_terms"],
                    "retraction_status": manifest_item["retraction_status"],
                    "display_rights": manifest_item["display_rights"]
                }
                
                qpoints.append({
                    "point_id": point_uuid,
                    "dense_vector": dense_vector,
                    "sparse_vector": sparse_vector,
                    "payload": payload
                })
            
            # Initialize / ensure Qdrant collection is ready
            self.qdrant.create_collection_if_missing("evidence_chunks_v1", dense_dim=1024)
            
            # Upsert vectors to Qdrant (mock or real client)
            if qpoints:
                self.qdrant.upsert_points("evidence_chunks_v1", qpoints)

            # EMBEDDED
            await self.db.update_status(pmcid, "EMBEDDED")
            await self.db.update_paper_metadata(
                pmcid, embedded_at=datetime.now().isoformat()
            )

            self.tracker.record_success()
            logger.debug("Completed %s with chunking & vector indexing", pmcid)

        except Exception as exc:
            logger.error("Failed to process %s: %s", pmcid, exc, exc_info=True)
            status_map = {
                "download": "FAILED_DOWNLOAD",
                "processing": "FAILED_PROCESSING",
                "embedding": "FAILED_EMBEDDING"
            }
            target_status = status_map.get(phase, "FAILED_DOWNLOAD")
            await self.db.update_status(
                pmcid, target_status, error_message=str(exc)[:500]
            )
            await self.db.increment_retry_count(pmcid)
            self.tracker.record_failure()

        finally:
            # Periodic throughput logging
            self._processed_count += 1
            if self._processed_count % _STATS_LOG_INTERVAL == 0:
                self.tracker.log_stats(logger)

    def _build_manifest_item(self, parsed: Any, xml_bytes: bytes, xml_path: Path) -> dict[str, Any]:
        """Convert a ParsedPaper structure into a validated Ingestion Manifest Item."""
        import re
        import hashlib
        from pipeline.parser import classify_evidence_category

        # Format date as YYYY-MM-DD
        pub_date = parsed.publication_date or ""
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", pub_date):
            if re.match(r"^\d{4}$", pub_date):
                pub_date = f"{pub_date}-01-01"
            elif re.match(r"^\d{4}-\d{2}$", pub_date):
                pub_date = f"{pub_date}-01"
            else:
                pub_date = "2020-01-01"  # Safe default

        sections_list = []
        ordinal = 1
        for name, text in parsed.sections.items():
            if not text or not text.strip():
                continue
            sections_list.append({
                "section_path": [name],
                "section_kind": name if name in ["introduction", "methods", "results", "discussion", "conclusion", "recommendations", "executive_summary"] else "other",
                "ordinal": ordinal,
                "text": text,
                "source_locator": {
                    "kind": "none",
                    "locator": {}
                }
            })
            ordinal += 1

        tables_list = list(parsed.tables)

        content_sha = hashlib.sha256(xml_bytes).hexdigest()
        evidence_category = classify_evidence_category(parsed.publication_types)

        item = {
            "source": "pmc",
            "pmid": parsed.pmid or "N/A",
            "pmcid": parsed.pmcid,
            "doi": parsed.doi or "N/A",
            "title": parsed.title or "Untitled",
            "authors": parsed.authors or [],
            "journal": parsed.journal or "Unknown",
            "publication_date": pub_date,
            "canonical_url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{parsed.pmcid}/",
            "display_rights": True,
            "retraction_status": "retracted" if parsed.is_excluded and "retract" in (parsed.exclusion_reason or "").lower() else "not_retracted",
            "content_sha256": content_sha,
            "parser_name": "jats_parser",
            "parser_version": "1.0.0",
            "source_artifact_uri": xml_path.as_uri(),
            "study_type": evidence_category if evidence_category in ["rct", "meta_analysis", "systematic_review", "guideline", "observational"] else "other",
            "mesh_terms": parsed.mesh_terms or [],
            "clinical_trial_ids": parsed.clinical_trial_ids or [],
            "sections": sections_list,
            "tables": tables_list,
            "evidence_card_fields": {
                "population": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "intervention": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "comparator": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "primary_outcome": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "effect_measure": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "effect_value": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} }
            }
        }
        return item

    # ------------------------------------------------------------------
    # Worker pool
    # ------------------------------------------------------------------

    async def worker(self, queue: asyncio.Queue) -> None:  # type: ignore[type-arg]
        """Consumer worker — pulls papers from *queue* until it is empty.

        Each paper is processed under the shared :attr:`semaphore` so the
        total number of in-flight downloads never exceeds ``max_workers``.
        """
        while True:
            try:
                paper = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            async with self.semaphore:
                try:
                    await self.process_paper(paper)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Worker error processing %s: %s",
                        paper.get("pmcid", "???"),
                        exc,
                        exc_info=True,
                    )
            queue.task_done()

    async def download_all(self, papers: list[dict]) -> None:
        """Launch a pool of async workers to download all *papers*.

        1. Populate an :class:`asyncio.Queue` with every paper dict.
        2. Spawn ``max_workers`` worker tasks.
        3. Wait until every item in the queue has been processed.
        4. Log final throughput statistics.

        Parameters
        ----------
        papers:
            List of paper dicts (as returned by
            :meth:`PaperDatabase.get_pending_papers`).
        """
        if not papers:
            logger.info("download_all: nothing to download")
            return

        queue: asyncio.Queue = asyncio.Queue()  # type: ignore[type-arg]
        for p in papers:
            queue.put_nowait(p)

        num_workers = min(self.config.pipeline.max_workers, len(papers))
        logger.info(
            "Starting download: %d papers with %d workers",
            len(papers),
            num_workers,
        )

        workers = [
            asyncio.create_task(self.worker(queue), name=f"dl-worker-{i}")
            for i in range(num_workers)
        ]

        # Wait for the queue to drain
        await queue.join()

        # Cancel workers that are still looping (all items consumed)
        for w in workers:
            w.cancel()

        # Suppress CancelledError from workers that were blocked
        results = await asyncio.gather(*workers, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.error("Worker %d raised: %s", idx, result)

        logger.info("Download phase complete — processed %d papers", len(papers))
        self.tracker.log_stats(logger)
