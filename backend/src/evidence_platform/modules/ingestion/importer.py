import uuid
import hashlib
import logging
from typing import Any
from pathlib import Path
from sqlalchemy.orm import Session

from ...db.models.models import (
    SourceDocument,
    DocumentRevision,
    DocumentSection,
    EvidenceUnit,
    Chunk,
    EvidenceCardData,
)
from ..corpus.chunker import SectionChunker, count_tokens
from .manifest_validator import validate_manifest_item

logger = logging.getLogger("evidence_platform.ingestion")

class CorpusImporter:
    """Imports validated document manifests into the canonical database schema."""

    def __init__(self, db_session: Session) -> None:
        self.db = db_session
        self.chunker = SectionChunker()

    def import_manifest_item(self, item: dict[str, Any], corpus_snapshot_id: str | None = None) -> str:
        """Validate and import a single manifest item.
        
        Returns the doc_id if imported or existing.
        """
        # 1. Schema validation
        validate_manifest_item(item)

        pmcid = item["pmcid"]
        pmid = item.get("pmid")
        doi = item.get("doi")
        title = item.get("title", "Untitled")
        source = item.get("source", "pmc")
        
        # 2. Check display rights and locators
        has_displayable_spans = True
        for section in item.get("sections", []):
            loc = section.get("source_locator", {})
            # If locator is missing or kind is 'none', we might not have a displayable span
            if not loc or loc.get("kind") == "none":
                has_displayable_spans = False

        # If display rights are forbidden, or we have no displayable spans,
        # the status will be marked as 'citation_not_ready'
        display_rights = item.get("display_rights", True)
        
        status = "published"
        if not display_rights or not has_displayable_spans:
            status = "citation_not_ready"

        # 3. Check if document already exists
        existing_doc = self.db.query(SourceDocument).filter_by(source=source, source_key=pmcid).first()
        if existing_doc:
            logger.info(f"SourceDocument for {pmcid} already exists. Skipping insertion.")
            return existing_doc.id

        # 4. Create SourceDocument
        doc_id = str(uuid.uuid4())
        doc = SourceDocument(
            id=doc_id,
            source=source,
            source_key=pmcid,
            pmid=pmid,
            pmcid=pmcid,
            doi=doi,
            canonical_url=item.get("canonical_url"),
            title=title,
            publication_date=item.get("publication_date", "2020-01-01"),
            retraction_status=item.get("retraction_status", "not_retracted"),
            display_rights=display_rights,
            mesh_terms=item.get("mesh_terms", []),
        )
        self.db.add(doc)

        # 5. Create DocumentRevision
        rev_id = str(uuid.uuid4())
        rev = DocumentRevision(
            id=rev_id,
            document_id=doc_id,
            corpus_snapshot_id=corpus_snapshot_id,
            revision_no=1,
            content_sha256=item.get("content_sha256", hashlib.sha256(title.encode()).hexdigest()),
            parser_version=item.get("parser_version", "1.0.0"),
            source_artifact_uri=item.get("source_artifact_uri", f"file:///mock/{pmcid}.xml"),
            study_type=item.get("study_type", "other"),
            revision_metadata=item.get("metadata", {}),
            status=status,
        )
        self.db.add(rev)

        # 6. Create DocumentSections, EvidenceUnits, and Chunks
        section_ordinal = 1
        for sec in item.get("sections", []):
            sec_text = sec["text"]
            if not sec_text or not sec_text.strip():
                continue

            sec_id = str(uuid.uuid4())
            section_kind = sec.get("section_kind", "other")
            section_path = sec.get("section_path", ["section"])
            
            section = DocumentSection(
                id=sec_id,
                revision_id=rev_id,
                section_path=section_path,
                section_kind=section_kind,
                ordinal=section_ordinal,
                text=sec_text,
                source_locator=sec.get("source_locator", {"kind": "none", "locator": {}}),
            )
            self.db.add(section)

            # Segment section text into parent-child chunks
            evidence_units = self.chunker.chunk_section(sec_text, f"{pmcid}_{section_kind}")
            
            eu_ordinal = 1
            for eu in evidence_units:
                eu_id = str(uuid.uuid4())
                evidence_unit = EvidenceUnit(
                    id=eu_id,
                    section_id=sec_id,
                    ordinal=eu_ordinal,
                    start_char=eu.start_char,
                    end_char=eu.end_char,
                    text=eu.text,
                    content_type="recommendation" if section_kind == "recommendations" else "prose",
                    token_count=eu.token_count,
                )
                self.db.add(evidence_unit)

                chunk_ordinal = 1
                for chunk in eu.child_chunks:
                    chunk_id = str(uuid.uuid4())
                    db_chunk = Chunk(
                        id=chunk_id,
                        evidence_unit_id=eu_id,
                        ordinal=chunk_ordinal,
                        start_char=chunk.start_char,
                        end_char=chunk.end_char,
                        text=chunk.text,
                        token_count=chunk.token_count,
                        embedding_model="BGE-M3",
                        embedding_version="1.0.0",
                        qdrant_point_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk.id)),
                        index_status="pending" if status == "published" else "failed",
                    )
                    self.db.add(db_chunk)
                    chunk_ordinal += 1

                eu_ordinal += 1
            section_ordinal += 1

        # 7. Create Tables
        for table_idx, table in enumerate(item.get("tables", [])):
            linearized_text = table["linearized_text"]
            if not linearized_text.strip():
                continue

            sec_id = str(uuid.uuid4())
            section = DocumentSection(
                id=sec_id,
                revision_id=rev_id,
                section_path=["tables", f"table_{table_idx + 1}"],
                section_kind="table",
                ordinal=section_ordinal,
                text=linearized_text,
                source_locator={"kind": "none", "locator": {}},
            )
            self.db.add(section)

            eu_id = str(uuid.uuid4())
            tok_count = count_tokens(linearized_text)
            
            evidence_unit = EvidenceUnit(
                id=eu_id,
                section_id=sec_id,
                ordinal=1,
                start_char=0,
                end_char=len(linearized_text),
                text=linearized_text,
                content_type="table",
                token_count=tok_count,
            )
            self.db.add(evidence_unit)

            chunk_id = str(uuid.uuid4())
            db_chunk = Chunk(
                id=chunk_id,
                evidence_unit_id=eu_id,
                ordinal=1,
                start_char=0,
                end_char=len(linearized_text),
                text=linearized_text,
                token_count=tok_count,
                embedding_model="BGE-M3",
                embedding_version="1.0.0",
                qdrant_point_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{pmcid}_table_{table_idx + 1:02d}_P01_C01")),
                index_status="pending" if status == "published" else "failed",
            )
            self.db.add(db_chunk)
            section_ordinal += 1

        # 8. Create Evidence Card Data
        card_fields = item.get("evidence_card_fields", {
            "population": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
            "intervention": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
            "comparator": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
            "primary_outcome": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
            "effect_measure": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
            "effect_value": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
        })
        card = EvidenceCardData(
            revision_id=rev_id,
            fields_json=card_fields,
            extraction_version="1.0.0",
        )
        self.db.add(card)

        self.db.commit()
        logger.info(f"Successfully imported {pmcid} with status {status}")
        return doc_id

    def import_manifest(self, manifest: dict[str, Any], corpus_snapshot_id: str | None = None) -> tuple[int, int]:
        """Import all items in a manifest."""
        items = manifest.get("items", [])
        inserted = 0
        skipped = 0
        for item in items:
            try:
                self.import_manifest_item(item, corpus_snapshot_id)
                inserted += 1
            except Exception as e:
                logger.error(f"Failed to import item {item.get('pmcid')}: {e}")
                self.db.rollback()
                skipped += 1
        return inserted, skipped
