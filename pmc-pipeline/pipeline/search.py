"""PubMed search via NCBI E-utilities.

Executes structured MeSH-based queries (RCT, Meta-Analysis, Systematic
Review) against PubMed using the History Server for efficient batch
retrieval of PMIDs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

import aiohttp

from pipeline.config import AppConfig
from pipeline.utils import TokenBucketRateLimiter, retry_with_backoff

logger = logging.getLogger("pmc_pipeline")


class PubMedSearcher:
    """Search PubMed for oncology paper PMIDs."""

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(
        self,
        config: AppConfig,
        rate_limiter: TokenBucketRateLimiter,
        session: aiohttp.ClientSession,
    ) -> None:
        self.config = config
        self.rate_limiter = rate_limiter
        self.session = session

    # -- public API -------------------------------------------------------

    async def search_all_queries(
        self, start_date: str | None = None
    ) -> list[str]:
        """Run every query defined in ``config.search.queries``.

        Parameters
        ----------
        start_date : str, optional
            Override date range start (format ``YYYY/MM/DD``).  When
            *None*, the last ``date_range_years`` from config are used.

        Returns
        -------
        list[str]
            Unique PMIDs across all queries.
        """
        date_filter = self._build_date_filter(start_date)
        all_pmids: set[str] = set()

        for name, query in self.config.search.queries.items():
            logger.info("Searching PubMed — %s …", name)
            pmids, _ = await self._search(query, date_filter)
            logger.info(
                "  %s: %d PMIDs (cumulative unique: %d)",
                name,
                len(pmids),
                len(all_pmids | set(pmids)),
            )
            all_pmids.update(pmids)

        logger.info("Total unique PMIDs across all queries: %d", len(all_pmids))
        return list(all_pmids)

    # -- internals --------------------------------------------------------

    def _build_date_filter(self, start_date: str | None = None) -> str:
        """Return a PubMed date-range filter clause."""
        if start_date:
            start = start_date
        else:
            years = self.config.search.date_range_years
            dt = datetime.now() - timedelta(days=years * 365)
            start = dt.strftime("%Y/%m/%d")
        return f'("{start}"[Date - Publication] : "3000"[Date - Publication])'

    @retry_with_backoff(max_retries=5, base_delay=2.0)
    async def _esearch(
        self, query: str, date_filter: str
    ) -> tuple[int, str, str]:
        """Post to ``esearch`` with History Server enabled.

        Returns (total_count, webenv, query_key).
        """
        # Clean query by replacing newlines with spaces and collapsing multiple spaces
        clean_q = " ".join(query.replace("\n", " ").replace("\r", " ").split())
        full_query = f"{clean_q} AND {date_filter}"
        params = {
            "db": "pubmed",
            "term": full_query,
            "usehistory": "y",
            "retmax": "0",
            "retmode": "xml",
            "tool": self.config.ncbi.tool_name,
        }
        if self.config.ncbi.api_key:
            params["api_key"] = self.config.ncbi.api_key
        if self.config.ncbi.email:
            params["email"] = self.config.ncbi.email

        await self.rate_limiter.acquire()
        async with self.session.get(
            f"{self.BASE_URL}/esearch.fcgi", params=params
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()

        root = ET.fromstring(text)
        count = int(root.findtext("Count", "0"))
        webenv = root.findtext("WebEnv", "")
        query_key = root.findtext("QueryKey", "")

        if not webenv or not query_key:
            raise ValueError("esearch did not return WebEnv / QueryKey")

        return count, webenv, query_key

    @retry_with_backoff(max_retries=5, base_delay=2.0)
    async def _efetch_pmids(
        self,
        webenv: str,
        query_key: str,
        retstart: int,
        retmax: int,
    ) -> list[str]:
        """Fetch a batch of PMIDs from the History Server."""
        params = {
            "db": "pubmed",
            "WebEnv": webenv,
            "query_key": query_key,
            "retstart": str(retstart),
            "retmax": str(retmax),
            "rettype": "uilist",
            "retmode": "text",
            "tool": self.config.ncbi.tool_name,
        }
        if self.config.ncbi.api_key:
            params["api_key"] = self.config.ncbi.api_key
        if self.config.ncbi.email:
            params["email"] = self.config.ncbi.email

        await self.rate_limiter.acquire()
        async with self.session.get(
            f"{self.BASE_URL}/efetch.fcgi", params=params
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()

        return [
            line.strip() for line in text.strip().splitlines() if line.strip()
        ]

    async def _search(
        self,
        query: str,
        date_filter: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[str], int]:
        """Full search: esearch + paginated efetch for PMIDs starting at offset, up to limit.

        Returns (list_of_pmids, total_search_count).
        """
        count, webenv, query_key = await self._esearch(query, date_filter)
        logger.info("  esearch returned %d results", count)

        if count == 0 or offset >= count or offset >= 9999:
            if offset >= 9999:
                logger.info("  Offset %d has reached the PubMed API retrieval limit (9,999). Skipping subsequent fetches.", offset)
            return [], count

        if limit is not None:
            max_retrieved = min(count, offset + limit)
        else:
            max_retrieved = count

        batch_size = self.config.pipeline.batch_size_search
        pmids: list[str] = []

        for start in range(offset, max_retrieved, batch_size):
            current_batch_size = min(batch_size, max_retrieved - start)
            batch = await self._efetch_pmids(
                webenv, query_key, start, current_batch_size
            )
            pmids.extend(batch)
            logger.info(
                "    fetched PMIDs %d–%d / %d",
                start + 1,
                min(start + current_batch_size, max_retrieved),
                count,
            )
            # small courtesy sleep between large batches
            if start + batch_size < max_retrieved:
                await asyncio.sleep(0.2)

        return pmids, count
