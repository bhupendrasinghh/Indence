#!/usr/bin/env python3
"""PMC Oncology Pipeline — Incremental Update.

Downloads papers published since the last successful run, using the
same four-stage flow as the full pipeline but scoped to a date window.

Usage::

    python update_papers.py                      # Since last run
    python update_papers.py --config config.yaml # Custom config
    python update_papers.py --since 2024/01/01   # Explicit start date
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

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

logger: logging.Logger | None = None


# ---------------------------------------------------------------------------
# Incremental update
# ---------------------------------------------------------------------------


async def update(args: argparse.Namespace) -> None:
    """Run an incremental update — download papers published since the last run.

    The start date is determined by (in priority order):

    1. ``--since`` CLI argument.
    2. Last-run date persisted by :class:`~pipeline.utils.CheckpointManager`.
    3. If neither is available the function exits with an error message
       advising the user to run the full pipeline first.

    Parameters
    ----------
    args:
        Parsed CLI arguments.
    """
    start_time = time.monotonic()

    # 1. Configuration -------------------------------------------------------
    config = load_config(args.config)
    config.paths.ensure_dirs()

    # 2. Logging -------------------------------------------------------------
    global logger  # noqa: PLW0603
    logger = setup_logging(config.paths.logs_dir)
    logger.info("=" * 60)
    logger.info("PMC Oncology Pipeline — Incremental Update")
    logger.info("=" * 60)

    # 3. Determine start date ------------------------------------------------
    checkpoint = CheckpointManager(config.paths.checkpoints_dir)

    if args.since:
        start_date: str = args.since
    else:
        last_run = checkpoint.get_last_run_date()
        if not last_run:
            logger.error(
                "No previous run found.  Run the full pipeline first:\n"
                "    python run_pipeline.py"
            )
            return
        start_date = last_run

    logger.info("Searching for papers published since: %s", start_date)

    # 4. Shared components ---------------------------------------------------
    rate_limiter = TokenBucketRateLimiter(config.ncbi.rate_limit)
    tracker = ThroughputTracker()

    db = PaperDatabase(config.paths.database)
    await db.connect()
    await db.initialize_schema()

    connector = aiohttp.TCPConnector(
        limit=config.pipeline.max_workers + 10,
        limit_per_host=20,
    )
    timeout = aiohttp.ClientTimeout(total=config.pipeline.request_timeout)

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout
    ) as session:
        try:
            # Stage 1 — Search for new papers --------------------------------
            searcher = PubMedSearcher(config, rate_limiter, session)
            pmids = await searcher.search_all_queries(start_date=start_date)
            logger.info("Found %d PMIDs since %s", len(pmids), start_date)

            # Stage 2 — Filter out already-known PMIDs -----------------------
            existing = await db.get_all_pmids()
            existing_set = (
                set(existing) if not isinstance(existing, set) else existing
            )
            new_pmids = [p for p in pmids if p not in existing_set]
            logger.info("%d new papers to process", len(new_pmids))

            if not new_pmids:
                logger.info("No new papers found — nothing to do.")
                checkpoint.save_last_run_date(
                    datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
                )
                return

            # Stage 2.5 — Filter PMIDs by species indexing -------------------
            sf = SpeciesFilter(config, rate_limiter, session)
            allowed_new, species_records = await sf.filter_pmids(new_pmids)

            if not allowed_new:
                logger.info("No new papers matching species criteria found — nothing to do.")
                checkpoint.save_last_run_date(
                    datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
                )
                return

            # Stage 3 — Convert PMIDs → PMCIDs -------------------------------
            converter = IDConverter(config, rate_limiter, session)
            paper_ids = await converter.convert_all(allowed_new)
            
            # Map species info to converted paper_ids
            for pid in paper_ids:
                rec = species_records.get(str(pid.pmid))
                if rec:
                    pid.is_human_study = rec.is_human_study
                    pid.is_animal_study = rec.is_animal_study
                    pid.publication_types = rec.publication_types
                    pid.mesh_terms = rec.mesh_terms

            logger.info(
                "%d papers with valid PMCIDs after conversion", len(paper_ids)
            )

            # Stage 4 — Check OA availability --------------------------------
            if paper_ids:
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
                            "download_url": (
                                oa.download_url if oa and oa.is_oa else None
                            ),
                            "license": oa.license if oa else None,
                            "is_human_study": pid.is_human_study,
                            "is_animal_study": pid.is_animal_study,
                            "publication_types": pid.publication_types,
                            "mesh_terms": pid.mesh_terms,
                        }
                    )

                inserted = await db.insert_papers_batch(papers_to_insert)
                logger.info("%d new papers inserted/updated in database", inserted)

                # Lock only these newly discovered papers for download
                inserted_pmcids = [p["pmcid"] for p in papers_to_insert if p.get("pmcid")]
                await db.mark_queued_for_download(inserted_pmcids)

            # Stage 5 — Download ---------------------------------------------
            pending = await db.get_pending_papers()
            if pending:
                logger.info("%d new papers pending download", len(pending))
                downloader = PMCDownloader(
                    config, db, rate_limiter, session, tracker
                )
                await downloader.download_all(pending)
            else:
                logger.info("No papers pending download")

        finally:
            tracker.log_stats(logger)

    # 5. Wrap-up -------------------------------------------------------------
    elapsed = time.monotonic() - start_time

    stats = await db.get_paper_count_by_status()
    await db.record_run(
        query=f"incremental_since_{start_date}",
        found=len(pmids),
        downloaded=stats.get("EMBEDDED", 0),
        failed=stats.get("FAILED_DOWNLOAD", 0) + stats.get("FAILED_PROCESSING", 0) + stats.get("FAILED_EMBEDDING", 0),
        duration=elapsed,
    )

    checkpoint.save_last_run_date(
        datetime.now(tz=timezone.utc).strftime("%Y/%m/%d")
    )
    logger.info("Final database stats: %s", stats)
    logger.info("Update complete in %.1fs (%.1f min)", elapsed, elapsed / 60)

    await db.close()
    logger.info("Done.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse command-line arguments and launch the incremental update."""
    parser = argparse.ArgumentParser(
        description="PMC Oncology Pipeline — Incremental Update",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--since",
        default=None,
        help="Override start date for the search (YYYY/MM/DD)",
    )
    args = parser.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(update(args))


if __name__ == "__main__":
    main()
