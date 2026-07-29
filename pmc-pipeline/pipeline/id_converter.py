"""Batch PMID → PMCID + DOI conversion via PMC ID Converter API.

The API accepts up to 200 identifiers per request.  Papers that lack
a PMCID mapping are silently dropped.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import aiohttp

from pipeline.config import AppConfig
from pipeline.utils import TokenBucketRateLimiter, retry_with_backoff

logger = logging.getLogger("pmc_pipeline")


@dataclass
class PaperID:
    """Lightweight triple returned by the ID converter."""

    pmid: str
    pmcid: str | None = None
    doi: str | None = None
    is_human_study: bool = False
    is_animal_study: bool = False
    publication_types: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)


class IDConverter:
    """Convert PubMed PMIDs to PMC PMCIDs (and DOIs) in batches."""

    BASE_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

    def __init__(
        self,
        config: AppConfig,
        rate_limiter: TokenBucketRateLimiter,
        session: aiohttp.ClientSession,
    ) -> None:
        self.config = config
        self.rate_limiter = rate_limiter
        self.session = session

    # -- public -----------------------------------------------------------

    async def convert_all(self, pmids: list[str]) -> list[PaperID]:
        """Convert *all* PMIDs, returning only those with a valid PMCID.

        PMIDs are processed in batches of ``batch_size_id_convert``
        (default 200).
        """
        batch_size = self.config.pipeline.batch_size_id_convert
        results: list[PaperID] = []
        total = len(pmids)

        for i in range(0, total, batch_size):
            chunk = pmids[i : i + batch_size]
            batch_results = await self._convert_batch(chunk)
            results.extend(batch_results)

            if (i // batch_size + 1) % 10 == 0 or i + batch_size >= total:
                logger.info(
                    "  ID conversion: %d / %d PMIDs processed → %d with PMCID",
                    min(i + batch_size, total),
                    total,
                    len(results),
                )

            # Small delay between batches to stay within rate limits
            await asyncio.sleep(0.05)

        valid = [p for p in results if p.pmcid]
        logger.info(
            "ID conversion complete: %d / %d PMIDs have valid PMCIDs",
            len(valid),
            total,
        )
        return valid

    # -- internals --------------------------------------------------------

    @retry_with_backoff(max_retries=5, base_delay=2.0)
    async def _convert_batch(self, pmids: list[str]) -> list[PaperID]:
        """Call the ID Converter API for a single batch (≤ 200 IDs)."""
        params = {
            "ids": ",".join(pmids),
            "format": "json",
            "idtype": "pmid",
            "tool": self.config.ncbi.tool_name,
        }
        if self.config.ncbi.email:
            params["email"] = self.config.ncbi.email
        if self.config.ncbi.api_key:
            params["api_key"] = self.config.ncbi.api_key

        await self.rate_limiter.acquire()
        async with self.session.get(self.BASE_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        records = data.get("records", [])
        results: list[PaperID] = []

        for rec in records:
            # Skip error records
            if rec.get("status") == "error" or "errmsg" in rec:
                continue

            pmid = str(rec.get("pmid", ""))
            pmcid = rec.get("pmcid")
            doi = rec.get("doi")

            if pmid and pmid != "None":
                results.append(PaperID(pmid=pmid, pmcid=pmcid, doi=doi))

        return results
