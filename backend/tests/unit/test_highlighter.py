import unittest
import sys
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.db.models.models import Base, SourceDocument, DocumentRevision, DocumentSection, EvidenceUnit, Chunk
from evidence_platform.modules.retrieval.retriever import RetrievalCandidate
from evidence_platform.modules.synthesis.highlighter import SpanHighlighter, split_sentences_with_offsets


class TestSpanHighlighter(unittest.TestCase):

    def setUp(self):
        # Database setup
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.highlighter = SpanHighlighter(self.session)

        # Setup test data
        self.doc_id = "doc-1"
        self.rev_id = "rev-1"
        self.sec_id = "sec-1"
        self.eu_id = "eu-1"
        self.chunk_id = "chunk-1"

        # Insert records with offsets
        # Section text: "This is sentence 1. And this is sentence 2! Finally sentence 3?"
        # EvidenceUnit starts at char 0
        # Chunk starts at char 0 relative to EU
        self.session.add(SourceDocument(id=self.doc_id, source="pmc", source_key="PMC111", title="A", publication_date="2022-01-01"))
        self.session.add(DocumentRevision(id=self.rev_id, document_id=self.doc_id, status="published", parser_version="1", content_sha256="s", study_type="rct", source_artifact_uri="file:///test.xml"))
        self.session.add(DocumentSection(id=self.sec_id, revision_id=self.rev_id, section_path=["A"], section_kind="results", ordinal=1, text="This is sentence 1. And this is sentence 2! Finally sentence 3.", source_locator={"kind": "none", "locator": {}}))
        self.session.add(EvidenceUnit(id=self.eu_id, section_id=self.sec_id, ordinal=1, start_char=5, end_char=58, text="is sentence 1. And this is sentence 2! Finally sentence 3.", token_count=10, content_type="prose"))
        self.session.add(Chunk(
            id=self.chunk_id, evidence_unit_id=self.eu_id, ordinal=1,
            start_char=3, end_char=50, text="sentence 1. And this is sentence 2! Finally sentence 3.",
            token_count=10, embedding_model="BGE-M3", embedding_version="1", index_status="indexed"
        ))
        self.session.commit()

        # Setup candidate map
        self.candidate = RetrievalCandidate(
            chunk_id=self.chunk_id, qdrant_point_id="pt-1",
            text="sentence 1. And this is sentence 2! Finally sentence 3.",
            score=0.9, document_id=self.doc_id, pmcid="PMC111",
            study_type="rct", publication_date="2022", display_rights=True
        )
        self.evidence_map = {"E01": self.candidate}

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_sentence_split_offsets(self):
        text = "This is S1. S2 is here! S3?"
        res = split_sentences_with_offsets(text)
        
        self.assertEqual(len(res), 3)
        self.assertEqual(res[0][0], "This is S1.")
        self.assertEqual(res[0][1], 0)
        self.assertEqual(res[0][2], 11)

        self.assertEqual(res[1][0], "S2 is here!")
        self.assertEqual(res[1][1], 12)
        self.assertEqual(res[1][2], 23)

        self.assertEqual(res[2][0], "S3?")
        self.assertEqual(res[2][1], 24)
        self.assertEqual(res[2][2], 27)

    def test_span_alignment(self):
        claims = [
            {
                "text": "Outcome matches sentence 2.",
                "sentence_ids": ["E01.S2"]
            }
        ]
        
        res = self.highlighter.align_claims(claims, self.evidence_map)
        
        self.assertEqual(len(res), 1)
        aligned = res[0]["aligned_spans"]
        self.assertEqual(len(aligned), 1)
        self.assertEqual(aligned[0]["text"], "And this is sentence 2!")
        
        # Check absolute offsets:
        # parent EvidenceUnit starts at: 5
        # Chunk starts relative to EU at: 3
        # Sentence 2 starts relative to Chunk at: 12 ("sentence 1. " has length 12)
        # So absolute start = 5 + 3 + 12 = 20!
        self.assertEqual(aligned[0]["section_start_char"], 20)
        self.assertEqual(aligned[0]["section_end_char"], 20 + len("And this is sentence 2!"))


if __name__ == "__main__":
    unittest.main()
