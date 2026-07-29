import unittest
import sys
import uuid
import datetime
from pathlib import Path
from typing import Any
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
    IngestionJob,
    IngestionRun,
)
from evidence_platform.workers.ingestion_worker import IngestionWorker
from evidence_platform.adapters.models.mock import MockEmbeddingClient
from evidence_platform.adapters.qdrant.qdrant_client import QdrantIndexClient
from evidence_platform.modules.ingestion.handlers import register_ingestion_handlers


class TestIndexHandler(unittest.TestCase):

    def setUp(self):
        # Database setup
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()

        # Worker setup
        self.worker = IngestionWorker(
            db_session_factory=self.Session,
            max_attempts=3,
            lease_duration_seconds=60,
            backoff_base_delay=5
        )

        # Clients setup (Qdrant client initialized with empty host runs in Mock mode)
        self.embedding_client = MockEmbeddingClient()
        self.qdrant_client = QdrantIndexClient(host="")

        # Register handlers
        register_ingestion_handlers(self.worker, self.embedding_client, self.qdrant_client)

        # Populate test fixtures
        self.run_id = "run-1"
        self.doc_id = "doc-1"
        self.rev_id = "rev-1"
        self.sec_id = "sec-1"
        self.eu_id = "eu-1"
        self.chunk_id = "chunk-1"
        self.qdrant_pid = str(uuid.uuid4())

        # Insert tables
        self.session.add(IngestionRun(id=self.run_id, status="running", manifest_checksum="abc"))
        self.session.add(SourceDocument(
            id=self.doc_id, source="pmc", source_key="PMC001",
            title="Ingestion Test", publication_date="2022-04-10",
            retraction_status="not_retracted", display_rights=True, mesh_terms=["Neoplasms"]
        ))
        self.session.add(DocumentRevision(
            id=self.rev_id, document_id=self.doc_id, revision_no=1,
            content_sha256="checksum", parser_version="1.0.0",
            source_artifact_uri="file:///test.xml", study_type="rct",
            revision_metadata={"biomarkers": ["HER2"], "subdomain": "breast"}, status="published"
        ))
        self.session.add(DocumentSection(
            id=self.sec_id, revision_id=self.rev_id, section_path=["Methods"],
            section_kind="methods", ordinal=1, text="Test methods paragraph.",
            source_locator={"kind": "none", "locator": {}}
        ))
        self.session.add(EvidenceUnit(
            id=self.eu_id, section_id=self.sec_id, ordinal=1,
            start_char=0, end_char=24, text="Test methods paragraph.",
            content_type="prose", token_count=4
        ))
        self.session.add(Chunk(
            id=self.chunk_id, evidence_unit_id=self.eu_id, ordinal=1,
            start_char=0, end_char=24, text="Test methods paragraph.",
            token_count=4, embedding_model="BGE-M3", embedding_version="1.0.0",
            qdrant_point_id=self.qdrant_pid, index_status="pending"
        ))
        self.session.add(IngestionJob(
            id="job-idx-1", run_id=self.run_id, document_id=self.doc_id,
            job_type="index", status="queued"
        ))
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_index_job_execution(self):
        # Claim and run the index job
        claimed_id = self.worker.claim_job()
        self.assertEqual(claimed_id, "job-idx-1")

        success = self.worker.process_job(claimed_id)
        self.assertTrue(success)

        # Clear session map cache
        self.session.expire_all()

        # Check job status
        job = self.session.query(IngestionJob).filter_by(id="job-idx-1").first()
        self.assertEqual(job.status, "succeeded")

        # Check chunk status
        chunk = self.session.query(Chunk).filter_by(id=self.chunk_id).first()
        self.assertEqual(chunk.index_status, "indexed")


if __name__ == "__main__":
    unittest.main()
