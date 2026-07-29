import unittest
import sys
import asyncio
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.db.models.models import (
    Base,
    SourceDocument,
    DocumentRevision,
    DocumentSection,
    EvidenceUnit,
    Chunk,
)
from evidence_platform.adapters.models.mock import MockEmbeddingClient
from evidence_platform.adapters.qdrant.qdrant_client import QdrantIndexClient
from evidence_platform.modules.retrieval.retriever import HybridRetriever


class TestHybridRetriever(unittest.TestCase):

    def setUp(self):
        # Database setup
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

        # Clients
        self.embedding_client = MockEmbeddingClient()
        self.qdrant_client = QdrantIndexClient(host="") # mock mode

        self.retriever = HybridRetriever(
            db_session=self.session,
            embedding_client=self.embedding_client,
            qdrant_client=self.qdrant_client
        )

        # Setup test data
        self.doc_id = "doc-100"
        self.rev_id = "rev-100"
        self.sec_id = "sec-100"
        self.eu_id = "eu-100"
        self.chunk_id = "chunk-100"

        # Insert records
        self.session.add(SourceDocument(
            id=self.doc_id, source="pmc", source_key="PMC100", pmcid="PMC100",
            title="Trastuzumab deruxtecan study", publication_date="2023-05-15",
            retraction_status="not_retracted", display_rights=True, mesh_terms=[]
        ))
        self.session.add(DocumentRevision(
            id=self.rev_id, document_id=self.doc_id, revision_no=1,
            content_sha256="sha", parser_version="1.0.0",
            source_artifact_uri="uri", study_type="rct",
            revision_metadata={}, status="published"
        ))
        self.session.add(DocumentSection(
            id=self.sec_id, revision_id=self.rev_id, section_path=["Results"],
            section_kind="results", ordinal=1, text="Trastuzumab deruxtecan is effective in breast cancer.",
            source_locator={"kind": "none", "locator": {}}
        ))
        self.session.add(EvidenceUnit(
            id=self.eu_id, section_id=self.sec_id, ordinal=1,
            start_char=0, end_char=52, text="Trastuzumab deruxtecan is effective in breast cancer.",
            content_type="prose", token_count=8
        ))
        self.session.add(Chunk(
            id=self.chunk_id, evidence_unit_id=self.eu_id, ordinal=1,
            start_char=0, end_char=52, text="Trastuzumab deruxtecan is effective in breast cancer.",
            token_count=8, embedding_model="BGE-M3", embedding_version="1.0.0",
            qdrant_point_id="pt-100", index_status="indexed"
        ))
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_rrf_rank_fusion(self):
        dense_results = ["docA", "docB", "docC"]
        sparse_results = ["docB", "docD", "docA"]

        # RRF formula: sum of 1 / (60 + rank)
        # docB rank in dense = 2 (idx 1), rank in sparse = 1 (idx 0)
        # score(docB) = 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522
        # docA rank in dense = 1, rank in sparse = 3
        # score(docA) = 1/(60+1) + 1/(60+3) = 1/61 + 1/63 = 0.016393 + 0.015873 = 0.032266
        
        scores = self.retriever._apply_rrf(dense_results, sparse_results)
        self.assertEqual(scores[0][0], "docB")
        self.assertEqual(scores[1][0], "docA")

    def test_retrieve_mock_fallback(self):
        # Retrieve using acronym expansion: t-dxd (trastuzumab deruxtecan) should match text
        candidates = asyncio.run(
            self.retriever.retrieve("effective dose of T-DXd in breast cancer", limit=5)
        )
        
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].chunk_id, self.chunk_id)
        self.assertEqual(candidates[0].pmcid, "PMC100")
        self.assertEqual(candidates[0].study_type, "rct")
        self.assertTrue(candidates[0].display_rights)

    def test_retrieve_filters_rct(self):
        # Match with RCT filter
        candidates = asyncio.run(
            self.retriever.retrieve("breast cancer", filters={"study_types": ["rct"]}, limit=5)
        )
        self.assertEqual(len(candidates), 1)

        # Exclude using meta-analysis filter
        candidates_empty = asyncio.run(
            self.retriever.retrieve("breast cancer", filters={"study_types": ["meta_analysis"]}, limit=5)
        )
        self.assertEqual(len(candidates_empty), 0)


if __name__ == "__main__":
    unittest.main()
