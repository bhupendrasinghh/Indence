"""Open Access availability filter via the PMC OA Web Service.

Queries ``oa.fcgi`` to determine whether a given PMCID is available in
the OA Subset, whether it is embargoed, and — if available — its
FTP/HTTPS download URL.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import aiohttp
from lxml import etree

from pipeline.config import AppConfig
from pipeline.utils import TokenBucketRateLimiter, retry_with_backoff

logger = logging.getLogger("pmc_pipeline")


@dataclass
class OARecord:
    """Result of an OA availability check for one PMCID."""

    pmcid: str
    is_oa: bool = False
    is_embargoed: bool = False
    download_url: str | None = None   # .tar.gz archive
    xml_url: str | None = None        # direct XML (rare)
    license: str | None = None


class OAFilter:
    """Check PMC Open Access availability for a list of PMCIDs."""

    OA_SERVICE_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi"

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

    async def check_oa_batch(self, pmcids: list[str]) -> list[OARecord]:
        """Check OA status for *all* PMCIDs with bounded concurrency.

        Returns one :class:`OARecord` per input PMCID (order not
        guaranteed).
        """
        sem = asyncio.Semaphore(self.config.pipeline.batch_size_oa_check)
        results: list[OARecord] = []
        total = len(pmcids)

        async def _guarded(pmcid: str) -> OARecord:
            async with sem:
                return await self._check_one(pmcid)

        # Launch all checks; the semaphore keeps concurrency bounded.
        tasks = [asyncio.create_task(_guarded(p)) for p in pmcids]

        done = 0
        for coro in asyncio.as_completed(tasks):
            rec = await coro
            results.append(rec)
            done += 1
            if done % 500 == 0 or done == total:
                oa_count = sum(1 for r in results if r.is_oa)
                logger.info(
                    "  OA check: %d / %d (OA: %d, embargoed: %d)",
                    done,
                    total,
                    oa_count,
                    sum(1 for r in results if r.is_embargoed),
                )

        return results

    async def filter_available(
        self, paper_ids: list
    ) -> tuple[list, list]:
        """Partition *paper_ids* into ``(available, unavailable)``.

        *available*:   OA **and** not embargoed (with download URL).
        *unavailable*: everything else.
        """
        pmcids = [getattr(p, "pmcid", p) for p in paper_ids]
        records = await self.check_oa_batch(pmcids)
        lookup = {r.pmcid: r for r in records}

        available: list = []
        unavailable: list = []

        for pid in paper_ids:
            pmcid = getattr(pid, "pmcid", pid)
            rec = lookup.get(pmcid)
            if rec and rec.is_oa and not rec.is_embargoed:
                available.append(pid)
            else:
                unavailable.append(pid)

        return available, unavailable

    # -- internals --------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def _check_one(self, pmcid: str) -> OARecord:
        """Query ``oa.fcgi`` for a single PMCID."""
        params = {"id": pmcid}

        await self.rate_limiter.acquire()
        async with self.session.get(
            self.OA_SERVICE_URL, params=params
        ) as resp:
            resp.raise_for_status()
            body = await resp.read()

        try:
            root = etree.fromstring(body)
        except etree.XMLSyntaxError:
            logger.warning("Malformed XML from oa.fcgi for %s", pmcid)
            return OARecord(pmcid=pmcid)

        # Check for error (not OA / embargoed)
        error_el = root.find(".//error")
        if error_el is not None:
            code = error_el.get("code", "")
            if "embargo" in code.lower():
                return OARecord(pmcid=pmcid, is_embargoed=True)
            # "idIsNotOpenAccess" or similar
            return OARecord(pmcid=pmcid)

        # Parse successful record
        record_el = root.find(".//record")
        if record_el is None:
            return OARecord(pmcid=pmcid)

        lic = record_el.get("license", "")

        download_url: str | None = None
        xml_url: str | None = None

        for link in record_el.findall("link"):
            fmt = link.get("format", "")
            href = link.get("href", "")
            # Prefer HTTPS over FTP
            href = href.replace(
                "ftp://ftp.ncbi.nlm.nih.gov", "https://ftp.ncbi.nlm.nih.gov"
            )
            if fmt == "tgz":
                download_url = href
            elif fmt == "xml":
                xml_url = href

        return OARecord(
            pmcid=pmcid,
            is_oa=True,
            download_url=download_url,
            xml_url=xml_url,
            license=lic,
        )
