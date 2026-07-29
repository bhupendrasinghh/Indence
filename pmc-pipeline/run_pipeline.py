#!/usr/bin/env python3
"""PMC Oncology Full-Text Download Pipeline — Main Entry Point.

Orchestrates the four-stage pipeline:

1. **Search**  — Discover oncology paper PMIDs via PubMed E-utilities.
2. **Convert** — Map PMIDs → PMCIDs (and DOIs) via the NCBI ID converter.
3. **OA Check** — Determine Open Access availability and download URLs.
4. **Download** — Retrieve full-text XML, parse, deduplicate, and persist.

Usage::

    python run_pipeline.py                    # Full run
    python run_pipeline.py --resume           # Resume interrupted run
    python run_pipeline.py --dry-run          # Search only, no downloads
    python run_pipeline.py --max-papers 100   # Limit total papers
    python run_pipeline.py --config path.yaml # Custom config file
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiohttp

from pipeline.config import load_config
from pipeline.database import PaperDatabase
from pipeline.downloader import PMCDownloader
from pipeline.id_converter import IDConverter
from pipeline.oa_filter import OAFilter
from pipeline.search import PubMedSearcher
from pipeline.species_filter import SpeciesFilter
from pipeline.utils import (
    CheckpointManager,
    ThroughputTracker,
    TokenBucketRateLimiter,
    setup_logging,
)

if TYPE_CHECKING:
    from pipeline.config import AppConfig

# Module-level logger — initialised inside ``run()``.
logger: logging.Logger | None = None


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


async def _stage_search(
    config: AppConfig,
    session: aiohttp.ClientSession,
    rate_limiter: TokenBucketRateLimiter,
    checkpoint: CheckpointManager,
    *,
    resume: bool,
) -> list[str]:
    """Stage 1 — Search PubMed for oncology paper PMIDs.

    If *resume* is ``True`` and a checkpoint exists, the cached PMID list
    is returned instead of re-querying NCBI.

    Returns
    -------
    list[str]
        Deduplicated list of PMIDs.
    """
    assert logger is not None
    logger.info("Stage 1: Searching PubMed for oncology papers…")

    if resume:
        cached = checkpoint.load_state("search")
        if cached and "pmids" in cached:
            pmids: list[str] = cached["pmids"]
            logger.info("Resumed: loaded %d PMIDs from checkpoint", len(pmids))
            return pmids

    searcher = PubMedSearcher(config, rate_limiter, session)
    pmids = await searcher.search_all_queries()
    checkpoint.save_state("search", {"pmids": pmids})
    logger.info("Stage 1 complete: %d unique PMIDs found", len(pmids))
    return pmids


async def _stage_species_filter(
    config: AppConfig,
    session: aiohttp.ClientSession,
    rate_limiter: TokenBucketRateLimiter,
    db: PaperDatabase,
    pmids: list[str],
) -> tuple[list[str], dict]:
    """Stage 1.5 — Verify species index metadata (Humans vs Animals).

    Returns
    -------
    tuple[list[str], dict]
        - A list of allowed PMID strings
        - A mapping from PMID to SpeciesRecord objects
    """
    assert logger is not None
    logger.info("Stage 1.5: Filtering PMIDs by MeSH species classification…")

    # Filter out PMIDs that are already in the database
    existing_pmids = await db.get_all_pmids()
    existing_set = set(existing_pmids) if not isinstance(existing_pmids, set) else existing_pmids
    new_pmids = [p for p in pmids if p not in existing_set]
    logger.info(
        "  %d PMIDs already in database, %d new to check for species",
        len(existing_set),
        len(new_pmids),
    )

    if not new_pmids:
        logger.info("Stage 1.5: No new PMIDs to check species for")
        return pmids, {}

    sf = SpeciesFilter(config, rate_limiter, session)
    allowed_new, records = await sf.filter_pmids(new_pmids)

    # Recombine with existing PMIDs (they are already allowed since they are in DB)
    allowed_pmids = [p for p in pmids if p in existing_set or p in allowed_new]
    return allowed_pmids, records


async def _stage_convert(
    config: AppConfig,
    session: aiohttp.ClientSession,
    rate_limiter: TokenBucketRateLimiter,
    db: PaperDatabase,
    pmids: list[str],
    species_records: dict,
    *,
    max_papers: int | None,
) -> list:
    """Stage 2 — Convert PMIDs to PMCIDs via the NCBI ID converter.

    Already-known PMIDs (present in the database) are skipped.  An optional
    ``max_papers`` cap is applied after conversion.

    Returns
    -------
    list[PaperID]
        Paper identifiers with valid PMCIDs.
    """
    assert logger is not None
    logger.info("Stage 2: Converting PMIDs to PMCIDs…")

    existing_pmids = await db.get_all_pmids()
    existing_set = set(existing_pmids) if not isinstance(existing_pmids, set) else existing_pmids
    new_pmids = [p for p in pmids if p not in existing_set]
    logger.info(
        "  %d PMIDs already in database, %d new to convert",
        len(existing_set),
        len(new_pmids),
    )

    if not new_pmids:
        logger.info("Stage 2: No new papers to convert")
        return []

    converter = IDConverter(config, rate_limiter, session)
    paper_ids = await converter.convert_all(new_pmids)

    # Map species information to converted paper_ids
    for pid in paper_ids:
        rec = species_records.get(str(pid.pmid))
        if rec:
            pid.is_human_study = rec.is_human_study
            pid.is_animal_study = rec.is_animal_study
            pid.publication_types = rec.publication_types
            pid.mesh_terms = rec.mesh_terms

    logger.info("Stage 2 complete: %d papers with valid PMCIDs", len(paper_ids))

    if max_papers and len(paper_ids) > max_papers:
        paper_ids = paper_ids[:max_papers]
        logger.info("Applied --max-papers limit: %d papers", max_papers)

    return paper_ids


async def _stage_oa_check(
    config: AppConfig,
    session: aiohttp.ClientSession,
    rate_limiter: TokenBucketRateLimiter,
    db: PaperDatabase,
    paper_ids: list,
) -> int:
    """Stage 3 — Check Open Access availability and insert into the database.

    Embargoed papers are silently skipped.

    Returns
    -------
    int
         Number of newly inserted papers.
    """
    assert logger is not None
    logger.info("Stage 3: Checking Open Access availability…")

    if not paper_ids:
        logger.info("Stage 3: No papers to check")
        return 0

    oa_filter = OAFilter(config, rate_limiter, session)
    pmcids = [p.pmcid for p in paper_ids if p.pmcid]
    oa_records = await oa_filter.check_oa_batch(pmcids)
    oa_lookup = {r.pmcid: r for r in oa_records}

    papers_to_insert: list[dict] = []
    for pid in paper_ids:
        oa = oa_lookup.get(pid.pmcid)
        if oa and oa.is_embargoed:
            continue
        papers_to_insert.append(
            {
                "pmid": pid.pmid,
                "pmcid": pid.pmcid,
                "doi": pid.doi,
                "download_url": oa.download_url if oa and oa.is_oa else None,
                "license": oa.license if oa else None,
                "is_human_study": pid.is_human_study,
                "is_animal_study": pid.is_animal_study,
                "publication_types": pid.publication_types,
                "mesh_terms": pid.mesh_terms,
            }
        )

    inserted = await db.insert_papers_batch(papers_to_insert)
    logger.info("Stage 3 complete: %d new papers queued for download", inserted)
    return inserted


async def _stage_download(
    config: AppConfig,
    session: aiohttp.ClientSession,
    rate_limiter: TokenBucketRateLimiter,
    db: PaperDatabase,
    tracker: ThroughputTracker,
    *,
    max_papers: int | None,
) -> None:
    """Stage 4 — Download, parse, and persist full-text papers."""
    assert logger is not None
    logger.info("Stage 4: Downloading full-text papers…")

    pending = await db.get_pending_papers(limit=max_papers)
    logger.info("  %d papers to download", len(pending))

    if not pending:
        logger.info("Stage 4: Nothing to download")
        return

    downloader = PMCDownloader(config, db, rate_limiter, session, tracker)
    await downloader.download_all(pending)


# ---------------------------------------------------------------------------
# Main async entry point
# ---------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> None:
    """Execute the full four-stage pipeline.

    Parameters
    ----------
    args:
        Parsed CLI arguments (see :func:`main`).
    """
    start_time = time.monotonic()

    # 1. Configuration -------------------------------------------------------
    config = load_config(args.config)
    config.paths.ensure_dirs()

    # 2. Logging -------------------------------------------------------------
    global logger  # noqa: PLW0603
    logger = setup_logging(config.paths.logs_dir)
    logger.info("=" * 60)
    logger.info("PMC Oncology Pipeline — Starting")
    logger.info("=" * 60)

    # 3. Shared components ---------------------------------------------------
    rate_limiter = TokenBucketRateLimiter(config.ncbi.rate_limit)
    tracker = ThroughputTracker()
    checkpoint = CheckpointManager(config.paths.checkpoints_dir)

    db = PaperDatabase(config.paths.database)
    await db.connect()
    await db.initialize_schema()

    if args.resume:
        await db.reset_downloading_to_pending()
        logger.info("Resumed: reset stuck 'downloading' papers to 'pending'")

    # 4. HTTP session --------------------------------------------------------
    connector = aiohttp.TCPConnector(
        limit=config.pipeline.max_workers + 10,
        limit_per_host=20,
    )
    timeout = aiohttp.ClientTimeout(total=config.pipeline.request_timeout)

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout
    ) as session:
        try:
            pmids = []
            stats = {}
            if args.mode == "discover":
                # 1. Load checkpoints
                discovery_state = checkpoint.load_state("discovery_state") or {}
                offsets = discovery_state.get("offsets", {})

                # Track total cataloged in this run for statistics
                total_cataloged = 0
                budget = args.limit if args.limit is not None else config.pipeline.discovery_limit

                logger.info(
                    "Executing Mode: DISCOVER (limit=%s, dry_run=%s)",
                    budget if budget is not None else "None",
                    args.dry_run,
                )

                searcher = PubMedSearcher(config, rate_limiter, session)
                date_filter = searcher._build_date_filter()

                for qname, qterm in config.search.queries.items():
                    if budget is not None and budget <= 0:
                        logger.info("Discovery budget fully exhausted.")
                        break

                    offset = offsets.get(qname, 0)
                    logger.info("Query '%s': checking PubMed starting from offset %d …", qname, offset)

                    # Get chunk limit
                    chunk_limit = budget if budget is not None else None

                    # Stage 1: Search & Fetch matching PMIDs
                    chunk_pmids, total_count = await searcher._search(
                        qterm, date_filter, offset=offset, limit=chunk_limit
                    )

                    if not chunk_pmids:
                        logger.info("Query '%s': no new papers to discover.", qname)
                        continue

                    logger.info(
                        "Query '%s': retrieved %d PMIDs (offset: %d, total matching: %d)",
                        qname,
                        len(chunk_pmids),
                        offset,
                        total_count,
                    )

                    pmids.extend(chunk_pmids)

                    if args.dry_run:
                        logger.info("DRY RUN — skipping metadata verification and cataloging.")
                        continue

                    # Streamed batch processing to keep memory bounded:
                    # Run the fetched PMIDs through Stage 1.5, Stage 2, and Stage 3
                    # Stage 1.5: Species Filtering
                    allowed_pmids, species_records = await _stage_species_filter(
                        config,
                        session,
                        rate_limiter,
                        db,
                        chunk_pmids,
                    )

                    # Stage 2: Convert PMIDs to PMCIDs
                    paper_ids = await _stage_convert(
                        config,
                        session,
                        rate_limiter,
                        db,
                        allowed_pmids,
                        species_records,
                        max_papers=None,
                    )

                    # Stage 3: Check OA and insert as DISCOVERED
                    inserted = await _stage_oa_check(
                        config,
                        session,
                        rate_limiter,
                        db,
                        paper_ids,
                    )

                    total_cataloged += inserted

                    # Update offset and save state
                    new_offset = offset + len(chunk_pmids)
                    offsets[qname] = new_offset
                    checkpoint.save_state("discovery_state", {"offsets": offsets})

                    if budget is not None:
                        budget -= len(chunk_pmids)

                    logger.info(
                        "Query '%s': progress updated (new offset: %d). %d new papers cataloged.",
                        qname,
                        new_offset,
                        inserted,
                    )

                logger.info("Discovery stage complete. Total cataloged in this run: %d", total_cataloged)

            elif args.mode == "expand":
                count = args.count if args.count is not None else config.pipeline.download_limit
                logger.info(
                    "Executing Mode: EXPAND (count=%s, sort=%s, range=%s-%s)",
                    count,
                    args.sort,
                    args.pub_start or "None",
                    args.pub_end or "None",
                )
                # 1. Select papers from DISCOVERED pool
                discovered = await db.get_discovered_pool(
                    limit=count,
                    sort_order=args.sort,
                    pub_start_year=args.pub_start,
                    pub_end_year=args.pub_end,
                )
                logger.info("Found %d papers in DISCOVERED pool to process", len(discovered))
                if not discovered:
                    logger.info("No discovered papers matching criteria available for expansion.")
                    return

                # 2. Lock them as QUEUED_FOR_DOWNLOAD
                pmcids = [p["pmcid"] for p in discovered if p.get("pmcid")]
                await db.mark_queued_for_download(pmcids)
                logger.info("Locked %d papers as QUEUED_FOR_DOWNLOAD", len(pmcids))

                # 3. Stage 4 — Download, parse, and persist ----------------------
                await _stage_download(
                    config,
                    session,
                    rate_limiter,
                    db,
                    tracker,
                    max_papers=args.max_papers,
                )

            elif args.mode == "update":
                logger.info("Executing Mode: UPDATE")
                from update_papers import update as run_update
                # Delegate to incremental update
                await run_update(args)
                return

        finally:
            if args.mode != "update":
                # Always log final stats, even on partial failure ----------------
                tracker.log_stats(logger)
                stats = await db.get_paper_count_by_status()
                logger.info("Final database stats: %s", stats)

    # 5. Wrap-up -------------------------------------------------------------
    if args.mode != "update":
        elapsed = time.monotonic() - start_time
        logger.info(
            "Pipeline completed in %.1fs (%.1f min)", elapsed, elapsed / 60
        )

        await db.record_run(
            query=f"mode_{args.mode}",
            found=len(pmids),
            downloaded=stats.get("EMBEDDED", 0),
            failed=stats.get("FAILED_DOWNLOAD", 0) + stats.get("FAILED_PROCESSING", 0) + stats.get("FAILED_EMBEDDING", 0),
            duration=elapsed,
        )

        checkpoint.save_last_run_date(
            datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
        )

        await db.close()
        logger.info("Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse command-line arguments and launch the pipeline."""
    parser = argparse.ArgumentParser(
        description="PMC Oncology Full-Text Download Pipeline",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume an interrupted run (loads checkpoints, resets stuck papers)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute search stage only — no downloads",
    )
    parser.add_argument(
        "--max-papers",
        type=int,
        default=None,
        help="Maximum number of papers to process (useful for testing)",
    )
    parser.add_argument(
        "--mode",
        choices=["discover", "expand", "update"],
        default="discover",
        help="Operation mode: 'discover' (search & catalog), 'expand' (download cataloged), 'update' (incremental search & download)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Number of papers to download in 'expand' mode (default: None, falls back to config.yaml)",
    )
    parser.add_argument(
        "--sort",
        choices=["newest", "oldest", "random"],
        default="newest",
        help="Order in which to select papers in 'expand' mode (default: newest)",
    )
    parser.add_argument(
        "--pub-start",
        type=int,
        default=None,
        help="Start year filter for publication date in 'expand' mode",
    )
    parser.add_argument(
        "--pub-end",
        type=int,
        default=None,
        help="End year filter for publication date in 'expand' mode",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Start date YYYY/MM/DD for incremental search in 'update' mode",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of papers to catalog in 'discover' mode (default: None, discover all)",
    )
    args = parser.parse_args()

    # Windows requires the Selector event loop for aiohttp compatibility.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
