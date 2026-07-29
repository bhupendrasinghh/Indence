"""JATS / NLM XML parser for PubMed Central full-text articles.

Extracts structured metadata, body sections, and references from the
JATS DTD used by PMC.  Also applies the post-download exclusion rules
(retracted, editorial, animal-only, etc.).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from pipeline.config import AppConfig

logger = logging.getLogger("pmc_pipeline")

# Canonical section-name mapping (lower-cased title → key).
_SECTION_MAP: dict[str, str] = {
    "introduction": "introduction",
    "background": "introduction",
    "introduction and background": "introduction",
    "methods": "methods",
    "method": "methods",
    "materials and methods": "methods",
    "patients and methods": "methods",
    "study design": "methods",
    "study population": "methods",
    "statistical analysis": "methods",
    "experimental procedures": "methods",
    "results": "results",
    "findings": "results",
    "discussion": "discussion",
    "discussion and conclusions": "discussion",
    "conclusion": "conclusion",
    "conclusions": "conclusion",
    "summary": "conclusion",
    "concluding remarks": "conclusion",
    # Guideline-specific sections
    "recommendations": "recommendations",
    "key recommendations": "recommendations",
    "practice recommendations": "recommendations",
    "clinical recommendations": "recommendations",
    "guideline recommendations": "recommendations",
    "summary of recommendations": "recommendations",
    "statements": "recommendations",
    "consensus statements": "recommendations",
    "strength of recommendation": "recommendations",
    "executive summary": "executive_summary",
    "summary of evidence": "executive_summary",
}


# ---------------------------------------------------------------------------
# Evidence-category classifier (shared with database.py)
# ---------------------------------------------------------------------------

def classify_evidence_category(publication_types: list[str]) -> str:
    """Derive a single evidence category from a list of PubMed publication types.

    Classification priority (highest first):
        guideline > meta_analysis > systematic_review > rct > other

    Parameters
    ----------
    publication_types:
        List of publication type strings as returned by PubMed efetch
        or JATS ``<subject>`` elements.

    Returns
    -------
    str
        One of ``'guideline'``, ``'meta_analysis'``, ``'systematic_review'``,
        ``'rct'``, or ``'other'``.
    """
    types_lower = {t.lower() for t in publication_types}

    if "practice guideline" in types_lower or "guideline" in types_lower:
        return "guideline"
    if "meta-analysis" in types_lower:
        return "meta_analysis"
    if "systematic review" in types_lower:
        return "systematic_review"
    if "randomized controlled trial" in types_lower:
        return "rct"
    return "other"

@dataclass
class ParsedPaper:
    """All fields extracted from a single JATS XML article."""

    pmid: str = ""
    pmcid: str = ""
    doi: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    journal: str = ""
    publication_date: str = ""
    article_type: str = ""
    license: str = ""
    abstract: str = ""
    sections: dict[str, str] = field(default_factory=lambda: {
        "introduction": "",
        "methods": "",
        "results": "",
        "discussion": "",
        "conclusion": "",
        "recommendations": "",
        "executive_summary": "",
    })
    references: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)
    mesh_terms: list[str] = field(default_factory=list)
    clinical_trial_ids: list[str] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    is_human_study: bool = False
    is_animal_study: bool = False
    is_excluded: bool = False
    exclusion_reason: str | None = None


class JATSParser:
    """Parse JATS XML into :class:`ParsedPaper` instances."""

    def __init__(self, config: AppConfig) -> None:
        self.excluded_types: list[str] = config.excluded_article_types
        self.allowed_types: list[str] = config.allowed_article_types

    # -- public API -------------------------------------------------------

    def parse(self, xml_content: str | bytes, pmcid: str) -> ParsedPaper:
        """Parse *xml_content* and return a :class:`ParsedPaper`.

        On parse failure an ``is_excluded`` paper with reason is returned
        rather than raising, so the caller can log and continue.
        """
        if isinstance(xml_content, str):
            xml_content = xml_content.encode("utf-8")

        try:
            root = etree.fromstring(xml_content)
        except etree.XMLSyntaxError as exc:
            logger.warning("XML parse error for %s: %s", pmcid, exc)
            return ParsedPaper(
                pmcid=pmcid,
                is_excluded=True,
                exclusion_reason=f"xml_parse_error: {exc}",
            )

        # Resolve the actual article element (root might be pmc-articleset)
        if root.tag != "article":
            article_el = root.find(".//article")
            if article_el is None:
                article_el = root
        else:
            article_el = root

        article_type = self._extract_article_type(article_el)
        is_excluded, reason = self._check_exclusions(article_el, article_type)

        paper = ParsedPaper(
            pmid=self._extract_pmid(article_el),
            pmcid=pmcid,
            doi=self._extract_doi(article_el),
            title=self._extract_title(article_el),
            authors=self._extract_authors(article_el),
            journal=self._extract_journal(article_el),
            publication_date=self._extract_date(article_el),
            article_type=article_type,
            license=self._extract_license(article_el),
            abstract=self._extract_abstract(article_el),
            sections=self._extract_sections(article_el),
            references=self._extract_references(article_el),
            publication_types=self._extract_publication_types(article_el),
            mesh_terms=self._extract_mesh_terms(article_el),
            clinical_trial_ids=self._extract_trial_ids(article_el),
            tables=self._extract_tables(article_el),
            is_excluded=is_excluded,
            exclusion_reason=reason,
        )
        return paper

    def to_json(self, paper: ParsedPaper) -> dict[str, Any]:
        """Serialise a :class:`ParsedPaper` to the project JSON schema."""
        return {
            "pmid": paper.pmid,
            "pmcid": paper.pmcid,
            "doi": paper.doi,
            "title": paper.title,
            "authors": paper.authors,
            "journal": paper.journal,
            "publication_date": paper.publication_date,
            "article_type": paper.article_type,
            "evidence_category": classify_evidence_category(paper.publication_types),
            "license": paper.license,
            "is_human_study": paper.is_human_study,
            "is_animal_study": paper.is_animal_study,
            "publication_types": paper.publication_types,
            "mesh_terms": paper.mesh_terms,
            "clinical_trial_ids": paper.clinical_trial_ids,
            "tables": paper.tables,
            "abstract": paper.abstract,
            "sections": paper.sections,
            "references": paper.references,
        }

    # -- extraction helpers -----------------------------------------------

    @staticmethod
    def _text(el: etree._Element | None) -> str:
        """Return the recursive text content of *el*, stripped."""
        if el is None:
            return ""
        return " ".join((el.itertext())).strip()

    @staticmethod
    def _all_text(el: etree._Element) -> str:
        """Recursively gather text, preserving paragraph breaks."""
        parts: list[str] = []
        for child in el.iter():
            if child.tag in ("p", "title") and child.text:
                parts.append("")  # paragraph separator
            if child.text:
                parts.append(child.text.strip())
            if child.tail:
                parts.append(child.tail.strip())
        return "\n".join(line for line in parts if line)

    def _extract_title(self, root: etree._Element) -> str:
        el = root.find(".//front//article-title")
        return self._text(el)

    def _extract_authors(self, root: etree._Element) -> list[str]:
        authors: list[str] = []
        for contrib in root.findall(".//front//contrib[@contrib-type='author']"):
            surname = contrib.findtext("name/surname", "")
            given = contrib.findtext("name/given-names", "")
            if surname:
                name = f"{surname} {given}".strip() if given else surname
                authors.append(name)
            else:
                # collab or string-name
                collab = contrib.findtext("collab", "")
                sname = self._text(contrib.find("string-name"))
                authors.append(collab or sname or "")
        return [a for a in authors if a]

    def _extract_journal(self, root: etree._Element) -> str:
        jt = root.findtext(".//front//journal-title", "")
        if not jt:
            jt = root.findtext(".//front//journal-id", "")
        return jt.strip()

    def _extract_date(self, root: etree._Element) -> str:
        """Try epub → ppub → ecollection → any pub-date."""
        for ptype in ("epub", "ppub", "ecollection"):
            el = root.find(f".//front//pub-date[@pub-type='{ptype}']")
            if el is None:
                el = root.find(f".//front//pub-date[@date-type='{ptype}']")
            if el is not None:
                return self._format_date(el)
        # fallback: any pub-date
        el = root.find(".//front//pub-date")
        if el is not None:
            return self._format_date(el)
        return ""

    @staticmethod
    def _format_date(el: etree._Element) -> str:
        y = el.findtext("year", "")
        m = el.findtext("month", "01").zfill(2)
        d = el.findtext("day", "01").zfill(2)
        if y:
            return f"{y}-{m}-{d}"
        return ""

    def _extract_article_type(self, root: etree._Element) -> str:
        return root.get("article-type", "")

    def _extract_abstract(self, root: etree._Element) -> str:
        abs_el = root.find(".//front//abstract")
        if abs_el is None:
            return ""
        # Structured abstracts have <sec> children
        secs = abs_el.findall("sec")
        if secs:
            parts: list[str] = []
            for sec in secs:
                title = sec.findtext("title", "")
                body = " ".join(
                    self._text(p) for p in sec.findall("p")
                )
                if title:
                    parts.append(f"{title}: {body}")
                else:
                    parts.append(body)
            return "\n".join(parts)
        return self._text(abs_el)

    def _extract_sec_text(self, el: etree._Element) -> str:
        """Recursively gather text from the current section element,
        excluding any text nested inside child <sec> or <table-wrap> elements.
        """
        parts: list[str] = []

        def traverse(node):
            if node.tag in ("sec", "table-wrap"):
                return

            # Add paragraph or title separator
            if node.tag in ("p", "title") and node.text:
                parts.append("")  # paragraph break

            if node.text:
                parts.append(node.text.strip())

            for child in node:
                traverse(child)

            if node.tail:
                parts.append(node.tail.strip())

        for child in el:
            traverse(child)

        return "\n".join(line for line in parts if line)

    def _extract_sections(self, root: etree._Element) -> dict[str, str]:
        result: dict[str, str] = {
            "introduction": "",
            "methods": "",
            "results": "",
            "discussion": "",
            "conclusion": "",
        }

        body = root.find(".//body")
        if body is None:
            return result

        def process_sec(sec: etree._Element, parent_canonical: str | None = None):
            title_el = sec.find("title")
            title_text = self._text(title_el).lower().strip() if title_el is not None else ""

            # Check sec-type
            sec_type = sec.get("sec-type", "").lower()
            canonical = _SECTION_MAP.get(sec_type)

            # Check title exact match
            if not canonical:
                canonical = _SECTION_MAP.get(title_text)

            # Check title substring match
            if not canonical:
                for key, val in _SECTION_MAP.items():
                    if key in title_text:
                        canonical = val
                        break

            # Inherit parent's type if none matched
            if not canonical:
                canonical = parent_canonical

            # Populate content if recognized
            if canonical and canonical in result:
                content = self._extract_sec_text(sec)
                if content:
                    if result[canonical]:
                        result[canonical] += "\n\n" + content
                    else:
                        result[canonical] = content

            # Recurse child sections
            for child_sec in sec.findall("sec"):
                process_sec(child_sec, canonical)

        for top_sec in body.findall("sec"):
            process_sec(top_sec, None)

        return result

    def _extract_tables(self, root: etree._Element) -> list[dict[str, Any]]:
        tables: list[dict[str, Any]] = []
        for tw in root.findall(".//table-wrap"):
            parts: list[str] = []
            
            # Extract caption
            caption_text = ""
            caption_el = tw.find(".//caption")
            if caption_el is not None:
                caption_text = self._text(caption_el)
                if caption_text:
                    parts.append(f"Table Caption: {caption_text}")

            headers = []
            rows = []
            
            # Find the actual <table> element
            table_el = tw.find(".//table")
            if table_el is not None:
                # Headers
                thead = table_el.find(".//thead")
                if thead is not None:
                    for tr in thead.findall(".//tr"):
                        tr_headers = [self._text(cell) for cell in tr.xpath(".//th | .//td")]
                        headers.extend(tr_headers)
                
                # Rows
                tbody = table_el.find(".//tbody")
                row_container = tbody if tbody is not None else table_el
                for tr in row_container.findall(".//tr"):
                    if thead is not None and tr in thead.xpath(".//tr"):
                        continue
                    row_cells = [self._text(cell) for cell in tr.xpath(".//th | .//td")]
                    if row_cells:
                        rows.append(row_cells)
                        parts.append(" | ".join(row_cells))
            else:
                for tr in tw.findall(".//tr"):
                    row_cells = [self._text(cell) for cell in tr.xpath(".//th | .//td")]
                    if row_cells:
                        rows.append(row_cells)
                        parts.append(" | ".join(row_cells))

            tables.append({
                "linearized_text": "\n".join(parts),
                "structured_json": {
                    "caption": caption_text,
                    "headers": headers,
                    "rows": rows
                }
            })
        return tables

    def _extract_trial_ids(self, root: etree._Element) -> list[str]:
        # Extract registry IDs (NCT numbers & ISRCTN numbers) from all text content
        text = self._text(root)
        matches = re.findall(r"\b(NCT\d{8}|ISRCTN\d{8})\b", text, re.IGNORECASE)
        norm_ids = [m.upper() for m in matches]
        unique_ids: list[str] = []
        for val in norm_ids:
            if val not in unique_ids:
                unique_ids.append(val)
        return unique_ids

    def _extract_mesh_terms(self, root: etree._Element) -> list[str]:
        terms = []
        for kwd in root.findall(".//front//kwd-group[@kwd-group-type='mesh']/kwd"):
            t = self._text(kwd)
            if t:
                terms.append(t)
        if not terms:
            for kwd in root.findall(".//front//kwd-group/kwd"):
                t = self._text(kwd)
                if t:
                    terms.append(t)
        # Deduplicate preserving order
        unique_terms = []
        for t in terms:
            if t not in unique_terms:
                unique_terms.append(t)
        return unique_terms

    def _extract_publication_types(self, root: etree._Element) -> list[str]:
        types = []
        for subj in root.findall(".//front//article-categories//subj-group//subject"):
            t = self._text(subj)
            if t:
                types.append(t)
        # Deduplicate preserving order
        unique_types = []
        for t in types:
            if t not in unique_types:
                unique_types.append(t)
        return unique_types

    def _extract_references(self, root: etree._Element) -> list[str]:
        refs: list[str] = []
        ref_list = root.find(".//back//ref-list")
        if ref_list is None:
            return refs

        for ref in ref_list.findall("ref"):
            citation = (
                ref.find("element-citation")
                or ref.find("mixed-citation")
                or ref.find("nlm-citation")
            )
            if citation is None:
                # Plain text ref
                text = self._text(ref)
                if text:
                    refs.append(text)
                continue

            # Build structured citation string
            authors_parts: list[str] = []
            for name in citation.findall(".//name"):
                s = name.findtext("surname", "")
                g = name.findtext("given-names", "")
                if s:
                    authors_parts.append(f"{s} {g}".strip())
            if not authors_parts:
                # Try person-group
                for pg in citation.findall("person-group"):
                    for name in pg.findall("name"):
                        s = name.findtext("surname", "")
                        g = name.findtext("given-names", "")
                        if s:
                            authors_parts.append(f"{s} {g}".strip())

            art_title = citation.findtext("article-title", "")
            source = citation.findtext("source", "")
            year = citation.findtext("year", "")
            volume = citation.findtext("volume", "")
            fpage = citation.findtext("fpage", "")

            parts = []
            if authors_parts:
                parts.append(", ".join(authors_parts[:3]))
                if len(authors_parts) > 3:
                    parts[-1] += " et al."
            if art_title:
                parts.append(art_title)
            if source:
                parts.append(source)
            if year:
                parts.append(f"({year})")
            if volume:
                vol_str = volume
                if fpage:
                    vol_str += f":{fpage}"
                parts.append(vol_str)

            refs.append(". ".join(parts))

        return refs

    def _extract_license(self, root: etree._Element) -> str:
        lic = root.find(".//front//license")
        if lic is not None:
            ltype = lic.get("license-type", "")
            href = lic.get("{http://www.w3.org/1999/xlink}href", "")
            text = self._text(lic)
            return ltype or href or text[:200]
        return ""

    def _extract_pmid(self, root: etree._Element) -> str:
        el = root.find(".//front//article-id[@pub-id-type='pmid']")
        return self._text(el)

    def _extract_doi(self, root: etree._Element) -> str:
        el = root.find(".//front//article-id[@pub-id-type='doi']")
        return self._text(el)

    # -- exclusion rules --------------------------------------------------

    def _check_exclusions(
        self, root: etree._Element, article_type: str
    ) -> tuple[bool, str | None]:
        """Apply exclusion criteria.  Returns ``(is_excluded, reason)``."""
        at_lower = article_type.lower()

        # 1. Article type in excluded list
        if at_lower in self.excluded_types:
            return True, f"excluded_article_type:{at_lower}"

        # 2. Article type not in allowed list (if list is non-empty)
        if self.allowed_types and at_lower not in (
            t.lower() for t in self.allowed_types
        ):
            return True, f"not_allowed_article_type:{at_lower}"

        # 3. Retraction notice
        if at_lower in ("retraction", "retraction-notice"):
            return True, "retraction_notice"
        if root.find(".//front//retraction") is not None:
            return True, "retracted"

        # 4. Preprint subject category
        for subj in root.findall(".//front//article-categories//subject"):
            if subj.text and "preprint" in subj.text.lower():
                return True, "preprint"

        # 5. Study protocol in title
        title = self._extract_title(root).lower()
        if re.search(r"\bstudy protocol\b", title) or re.search(
            r"\bprotocol\s+for\b", title
        ):
            return True, "study_protocol"

        return False, None
