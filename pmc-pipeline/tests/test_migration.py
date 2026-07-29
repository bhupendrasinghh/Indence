"""Tests for the ingestion pipeline target compliance features.

Verifies schema validation, chunking boundaries, parent-child sizes, and database
schema compatibility.
"""

from __future__ import annotations

import uuid
import jsonschema
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pipeline.chunker import SectionChunker, count_tokens, segment_sentences
from pipeline.manifest_validator import validate_manifest_item
from pipeline.models import (
    Base,
    SourceDocument,
    DocumentRevision,
    DocumentSection,
    EvidenceUnit,
    Chunk,
)

# 1. Validation Tests
def test_manifest_validation_valid():
    """Verify that a valid manifest item dictionary passes validation."""
    valid_item = {
        "source": "pmc",
        "pmid": "12345678",
        "pmcid": "PMC1234567",
        "doi": "10.1000/xyz123",
        "title": "Oncology Research Paper Title",
        "authors": ["Doe John", "Smith Jane"],
        "journal": "Journal of Clinical Oncology",
        "publication_date": "2024-05-12",
        "canonical_url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/",
        "display_rights": True,
        "retraction_status": "not_retracted",
        "content_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "parser_name": "jats_parser",
        "parser_version": "1.0.0",
        "source_artifact_uri": "file:///path/to/PMC1234567.xml",
        "study_type": "rct",
        "mesh_terms": ["Humans", "Neoplasms"],
        "clinical_trial_ids": ["NCT00000000"],
        "sections": [
            {
                "section_path": ["Introduction"],
                "section_kind": "introduction",
                "ordinal": 1,
                "text": "This is introductory text of the oncology paper.",
                "source_locator": {
                    "kind": "none",
                    "locator": {}
                }
            }
        ],
        "tables": []
    }
    
    # Should not raise validation error
    validate_manifest_item(valid_item)


def test_manifest_validation_invalid():
    """Verify that missing required keys raise jsonschema.ValidationError."""
    invalid_item = {
        "source": "pmc",
        "pmcid": "PMC1234567",
        # Missing title, publication_date, authors, etc.
    }
    try:
        validate_manifest_item(invalid_item)
        raise AssertionError("Expected jsonschema.ValidationError but none was raised")
    except jsonschema.ValidationError:
        pass


# 2. Chunker Tests
def test_chunker_sentence_splitting():
    """Verify sentences are split correctly with proper start/end character offsets."""
    text = "Trastuzumab deruxtecan showed clinical benefit in HER2-low mBC. This study was a randomized controlled trial. Let's inspect the results."
    sentences = segment_sentences(text)
    
    assert len(sentences) == 3
    assert sentences[0].text == "Trastuzumab deruxtecan showed clinical benefit in HER2-low mBC."
    assert sentences[1].text == "This study was a randomized controlled trial."
    assert sentences[2].text == "Let's inspect the results."
    
    # Check start and end offsets are correct relative to text
    for s in sentences:
        assert text[s.start_char:s.end_char] == s.text


def test_chunker_parent_child_groups():
    """Verify that chunker groups sections correctly into parent/child structures."""
    chunker = SectionChunker(child_max_tokens=60, parent_target_tokens=150)
    
    # 8 short sentences (approx 10-15 tokens each)
    section_text = (
        "Sentence one is here. Sentence two is here. Sentence three is here. "
        "Sentence four is here. Sentence five is here. Sentence six is here. "
        "Sentence seven is here. Sentence eight is here."
    )
    
    evidence_units = chunker.chunk_section(section_text, "PMC123_intro")
    
    assert len(evidence_units) >= 1
    for eu in evidence_units:
        assert eu.id.startswith("PMC123_intro_P")
        assert len(eu.child_chunks) >= 1
        
        # Verify children IDs are structured
        for child in eu.child_chunks:
            assert child.id.startswith(eu.id + "_C")
            assert len(child.text) > 0
            assert child.token_count > 0
            
            # Verify child offsets slice correctly
            assert section_text[child.start_char:child.end_char] == child.text


# 3. Database Schema Compatibility Tests
def test_database_schema_insertion():
    """Verify that SQLAlchemy schema models are compatible and support insert/queries."""
    # Use in-memory SQLite to test schema compliance and SQL compatibility
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # 1. Create source doc
    doc_id = str(uuid.uuid4())
    doc = SourceDocument(
        id=doc_id,
        source="pubmed",
        source_key="12345",
        pmid="12345",
        pmcid="PMC12345",
        doi="10.1000/abc123",
        title="Study on HER2 breast cancer",
        publication_date="2023-01-01",
        retraction_status="not_retracted",
        display_rights=True,
        mesh_terms=["Humans", "Oncology"]
    )
    session.add(doc)
    
    # 2. Create revision
    rev_id = str(uuid.uuid4())
    rev = DocumentRevision(
        id=rev_id,
        document_id=doc_id,
        revision_no=1,
        content_sha256="abc123hash",
        parser_version="1.0.0",
        source_artifact_uri="file:///artifact/path.xml",
        study_type="rct",
        metadata_json={}
    )
    session.add(rev)
    
    # 3. Create section
    sec_id = str(uuid.uuid4())
    sec = DocumentSection(
        id=sec_id,
        revision_id=rev_id,
        section_path=["Results"],
        section_kind="results",
        ordinal=1,
        text="The median PFS was 10 months versus 5 months.",
        source_locator={"kind": "none", "locator": {}}
    )
    session.add(sec)
    
    session.commit()
    
    # Fetch back and assert
    fetched_doc = session.query(SourceDocument).filter_by(id=doc_id).first()
    assert fetched_doc is not None
    assert fetched_doc.title == "Study on HER2 breast cancer"
    assert fetched_doc.mesh_terms == ["Humans", "Oncology"]
    
    fetched_rev = session.query(DocumentRevision).filter_by(id=rev_id).first()
    assert fetched_rev is not None
    assert fetched_rev.study_type == "rct"
    
    fetched_sec = session.query(DocumentSection).filter_by(id=sec_id).first()
    assert fetched_sec is not None
    assert fetched_sec.section_kind == "results"
    
    session.close()

if __name__ == "__main__":
    import sys
    print("Running tests/test_migration.py...")
    try:
        test_manifest_validation_valid()
        print("  - test_manifest_validation_valid: PASSED")
        test_manifest_validation_invalid()
        print("  - test_manifest_validation_invalid: PASSED")
        test_chunker_sentence_splitting()
        print("  - test_chunker_sentence_splitting: PASSED")
        test_chunker_parent_child_groups()
        print("  - test_chunker_parent_child_groups: PASSED")
        test_database_schema_insertion()
        print("  - test_database_schema_insertion: PASSED")
        print("\nAll tests completed successfully!")
    except AssertionError as exc:
        print(f"\nTest failed: AssertionError: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\nTest failed with exception: {exc}")
        sys.exit(1)
