"""Batch PubMed metadata fetcher and species classifier.

Retrieves MeSH headings for a list of PMIDs via E-utilities efetch (POST,
db=pubmed) in batches of 1,000.  Classifies papers into Humans vs Animals,
and filters out non-human studies before ID conversion and full-text download.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

import aiohttp

from pipeline.config import AppConfig
from pipeline.utils import TokenBucketRateLimiter, retry_with_backoff

logger = logging.getLogger("pmc_pipeline")


@dataclass
class SpeciesRecord:
    """Metadata regarding species classification for a single PMID."""

    pmid: str
    is_human_study: bool = False
    is_animal_study: bool = False
    is_unindexed: bool = False
    mesh_terms: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)


class SpeciesFilter:
    """Retrieve MeSH classification from PubMed and filter out animal studies."""

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
        self.allow_mixed = config.species.allow_mixed_species
        self.allow_unindexed = config.species.allow_unindexed_species

    # -- public API -------------------------------------------------------

    async def filter_pmids(
        self, pmids: list[str]
    ) -> tuple[list[str], dict[str, SpeciesRecord]]:
        """Verify species index metadata for *pmids* and filter them.

        PMIDs are fetched in chunks of 1,000.

        Returns
        -------
        tuple[list[str], dict[str, SpeciesRecord]]
            - Allowed PMID strings.
            - A lookup mapping of PMID to its species metadata record.
        """
        if not pmids:
            return [], {}

        total = len(pmids)
        batch_size = 1000
        records: dict[str, SpeciesRecord] = {}

        logger.info(
            "Fetching species MeSH metadata for %d PMIDs…",
            total,
        )

        for i in range(0, total, batch_size):
            chunk = pmids[i : i + batch_size]
            batch_records = await self._fetch_batch(chunk)
            records.update(batch_records)

            if (i // batch_size + 1) % 5 == 0 or i + batch_size >= total:
                logger.info(
                    "  Species check progress: %d / %d PMIDs processed",
                    min(i + batch_size, total),
                    total,
                )

            # small delay between batches
            await asyncio.sleep(0.05)

        allowed_pmids: list[str] = []
        excluded_count = 0
        mixed_count = 0
        unindexed_count = 0
        human_only_count = 0

        for pmid in pmids:
            rec = records.get(pmid)
            if not rec:
                rec = SpeciesRecord(pmid=pmid, is_unindexed=True)
                records[pmid] = rec

            if rec.is_unindexed:
                if self.allow_unindexed:
                    allowed_pmids.append(pmid)
                    unindexed_count += 1
                else:
                    excluded_count += 1
            elif rec.is_human_study and not rec.is_animal_study:
                allowed_pmids.append(pmid)
                human_only_count += 1
            elif rec.is_human_study and rec.is_animal_study:
                if self.allow_mixed:
                    allowed_pmids.append(pmid)
                    mixed_count += 1
                else:
                    excluded_count += 1
            else:
                # Animal-only study
                excluded_count += 1

        logger.info(
            "Species filter complete: %d PMIDs allowed, %d excluded. "
            "(Human-only: %d, Mixed: %d, Unindexed: %d)",
            len(allowed_pmids),
            excluded_count,
            human_only_count,
            mixed_count,
            unindexed_count,
        )

        return allowed_pmids, records

    # -- internals --------------------------------------------------------

    @retry_with_backoff(max_retries=5, base_delay=2.0)
    async def _fetch_batch(self, pmids: list[str]) -> dict[str, SpeciesRecord]:
        """Call efetch POST to fetch PubmedArticle records for a batch."""
        params = {
            "db": "pubmed",
            "retmode": "xml",
        }
        ncbi = self.config.ncbi
        if ncbi.api_key:
            params["api_key"] = ncbi.api_key
        if ncbi.tool_name:
            params["tool"] = ncbi.tool_name
        if ncbi.email:
            params["email"] = ncbi.email

        # Send ID list via POST to prevent HTTP 414 Request-URI Too Large
        data = {"id": ",".join(pmids)}

        await self.rate_limiter.acquire()
        async with self.session.post(
            f"{self.BASE_URL}/efetch.fcgi", params=params, data=data
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()

        try:
            root = ET.fromstring(text.encode("utf-8"))
        except ET.ParseError as exc:
            logger.warning("Failed to parse PubmedArticleSet XML: %s", exc)
            return {}

        records: dict[str, SpeciesRecord] = {}

        for art in root.findall(".//PubmedArticle"):
            pmid_el = art.find(".//PMID")
            if pmid_el is None or not pmid_el.text:
                continue
            pmid = pmid_el.text.strip()

            is_human = False
            is_animal = False
            has_mesh = False
            mesh_terms_list: list[str] = []

            mesh_headings = art.findall(".//MeshHeadingList/MeshHeading")
            if mesh_headings:
                has_mesh = True
                for mh in mesh_headings:
                    desc_el = mh.find("DescriptorName")
                    if desc_el is not None and desc_el.text:
                        term = desc_el.text.strip()
                        mesh_terms_list.append(term)
                        term_lower = term.lower()
                        if term_lower == "humans":
                            is_human = True
                        elif term_lower == "animals":
                            is_animal = True

            publication_types_list: list[str] = []
            pub_types_el = art.findall(".//PublicationTypeList/PublicationType")
            for pt in pub_types_el:
                if pt.text:
                    publication_types_list.append(pt.text.strip())

            records[pmid] = SpeciesRecord(
                pmid=pmid,
                is_human_study=is_human,
                is_animal_study=is_animal,
                is_unindexed=not has_mesh,
                mesh_terms=mesh_terms_list,
                publication_types=publication_types_list,
            )

        return records
