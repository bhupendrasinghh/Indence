"""Migration script to backfill SQLite/PostgreSQL target tables from existing downloaded corpus.

Reads flat SQLite 'papers.db', parses raw XML files, segments them into
parent/child units, and writes them to the new schema.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.config import load_config
from pipeline.parser import JATSParser, classify_evidence_category
from pipeline.chunker import SectionChunker
from pipeline.models import (
    Base,
    SourceDocument,
    DocumentRevision,
    DocumentSection,
    EvidenceUnit,
    Chunk,
    EvidenceCardData,
)

def run_migration(db_url: str | None = None) -> None:
    config = load_config(str(PROJECT_ROOT / "config.yaml"))
    
    # 1. Connect to SQLite source
    sqlite_db_path = Path(config.paths.database)
    if not sqlite_db_path.exists():
        print(f"Source SQLite database not found at {sqlite_db_path}. Exiting.")
        return
        
    print(f"Connecting to source SQLite DB: {sqlite_db_path}")
    src_conn = sqlite3.connect(str(sqlite_db_path))
    src_conn.row_factory = sqlite3.Row
    
    # 2. Connect to target database
    if not db_url:
        # Default to a local SQLite database that mirrors the target PostgreSQL schemas
        # for testing and local backward compatibility
        target_db_path = sqlite_db_path.parent / "papers_postgres_mirror.db"
        db_url = f"sqlite:///{target_db_path}"
        
    print(f"Target Database URL: {db_url}")
    engine = create_engine(db_url)
    
    # Create target tables
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # Initialize parser and chunker
    parser = JATSParser(config)
    chunker = SectionChunker()
    
    # Fetch all processed/embedded papers from source SQLite
    rows = src_conn.execute(
        "SELECT * FROM papers WHERE status IN ('EMBEDDED', 'PROCESSED')"
    ).fetchall()
    
    print(f"Found {len(rows)} papers to migrate.")
    
    migrated_count = 0
    for idx, row in enumerate(rows):
        pmcid = row["pmcid"]
        pmid = row["pmid"]
        doi = row["doi"]
        
        # Check if already migrated
        existing = session.query(SourceDocument).filter_by(pmcid=pmcid).first()
        if existing:
            print(f"[{idx+1}/{len(rows)}] Paper {pmcid} already migrated. Skipping.")
            continue
            
        # Path to raw XML
        xml_path = Path(config.paths.xml_dir) / f"{pmcid}.xml"
        if not xml_path.exists():
            print(f"[{idx+1}/{len(rows)}] Raw XML file not found for {pmcid} at {xml_path}. Skipping.")
            continue
            
        # Read XML
        try:
            with open(xml_path, "rb") as f:
                xml_bytes = f.read()
        except Exception as e:
            print(f"Error reading {xml_path}: {e}")
            continue
            
        # Parse XML
        parsed = parser.parse(xml_bytes, pmcid)
        if parsed.is_excluded:
            print(f"Paper {pmcid} is excluded during re-parse. Reason: {parsed.exclusion_reason}. Skipping.")
            continue
            
        # Reconstruct date or default
        pub_date = parsed.publication_date or "2020-01-01"
        if len(pub_date) == 4:
            pub_date = f"{pub_date}-01-01"
        elif len(pub_date) == 7:
            pub_date = f"{pub_date}-01"
            
        # 3. Create Source Document
        doc_id = str(uuid.uuid4())
        doc = SourceDocument(
            id=doc_id,
            source="pmc",
            source_key=pmcid,
            pmid=pmid,
            pmcid=pmcid,
            doi=doi,
            canonical_url=f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
            title=parsed.title or "Untitled",
            publication_date=pub_date,
            retraction_status="not_retracted",
            display_rights=True,
            mesh_terms=parsed.mesh_terms or [],
        )
        session.add(doc)
        
        # 4. Create Document Revision
        rev_id = str(uuid.uuid4())
        import hashlib
        content_hash = hashlib.sha256(xml_bytes).hexdigest()
        evidence_category = classify_evidence_category(parsed.publication_types)
        
        rev = DocumentRevision(
            id=rev_id,
            document_id=doc_id,
            revision_no=1,
            content_sha256=content_hash,
            parser_version="1.0.0",
            source_artifact_uri=xml_path.as_uri(),
            study_type=evidence_category if evidence_category in ["rct", "meta_analysis", "systematic_review", "guideline", "observational"] else "other",
            metadata_json={},
            status="published"
        )
        session.add(rev)
        
        # 5. Create Document Sections and Parent-Child Chunks
        section_ordinal = 1
        for name, text in parsed.sections.items():
            if not text or not text.strip():
                continue
                
            sec_id = str(uuid.uuid4())
            section = DocumentSection(
                id=sec_id,
                revision_id=rev_id,
                section_path=[name],
                section_kind=name if name in ["introduction", "methods", "results", "discussion", "conclusion", "recommendations", "executive_summary"] else "other",
                ordinal=section_ordinal,
                text=text,
                source_locator={"kind": "none", "locator": {}}
            )
            session.add(section)
            
            # Chunk the section
            evidence_units = chunker.chunk_section(text, f"{pmcid}_{name}")
            
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
                    content_type="recommendation" if name == "recommendations" else "prose",
                    token_count=eu.token_count
                )
                session.add(evidence_unit)
                
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
                        index_status="indexed"
                    )
                    session.add(db_chunk)
                    chunk_ordinal += 1
                    
                eu_ordinal += 1
            section_ordinal += 1
            
        # 5.5. Create Document Sections and Chunks for Tables
        for table_idx, table in enumerate(parsed.tables):
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
                source_locator={"kind": "none", "locator": {}}
            )
            session.add(section)
            
            eu_id = str(uuid.uuid4())
            from pipeline.chunker import count_tokens
            tok_count = count_tokens(linearized_text)
            
            evidence_unit = EvidenceUnit(
                id=eu_id,
                section_id=sec_id,
                ordinal=1,
                start_char=0,
                end_char=len(linearized_text),
                text=linearized_text,
                content_type="table",
                token_count=tok_count
            )
            session.add(evidence_unit)
            
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
                index_status="indexed"
            )
            session.add(db_chunk)
            section_ordinal += 1
            
        # 6. Add Empty Evidence Card fields
        card = EvidenceCardData(
            revision_id=rev_id,
            fields_json={
                "population": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "intervention": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "comparator": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "primary_outcome": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "effect_measure": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} },
                "effect_value": { "value": "Not extracted", "confidence": "Not extracted", "source_locator": {} }
            },
            extraction_version="1.0.0"
        )
        session.add(card)
        
        session.commit()
        migrated_count += 1
        print(f"[{idx+1}/{len(rows)}] Successfully migrated paper {pmcid}")
        
    session.close()
    src_conn.close()
    print(f"\nMigration successfully completed. Migrated {migrated_count} documents.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate SQLite data to target relational schema")
    parser.add_argument("--db-url", type=str, default=None, help="Target PostgreSQL DB URL (default: SQLite file mirror)")
    args = parser.parse_args()
    run_migration(args.db_url)
