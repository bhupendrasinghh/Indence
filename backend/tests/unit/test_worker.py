import unittest
import sys
import datetime
from pathlib import Path
from typing import Any
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from evidence_platform.db.models.models import Base, IngestionJob, IngestionRun, SourceDocument
from evidence_platform.workers.ingestion_worker import IngestionWorker


class TestIngestionWorker(unittest.TestCase):

    def setUp(self):
        # Setup in-memory sqlite DB
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        
        # Setup worker
        self.worker = IngestionWorker(
            db_session_factory=self.Session,
            max_attempts=3,
            lease_duration_seconds=60,
            backoff_base_delay=5
        )

        # Setup test data: a run and a document
        self.run_id = "run-123"
        self.doc_id = "doc-456"
        
        self.session.add(IngestionRun(id=self.run_id, status="running", manifest_checksum="mock_checksum"))
        self.session.add(SourceDocument(id=self.doc_id, source="pmc", source_key="PMC123", title="Test", publication_date="2020-01-01"))
        self.session.commit()

        self.handler_calls = []
        self.worker.register_handler("mock_task", self._mock_handler)

    def _mock_handler(self, job: IngestionJob, session: Any) -> None:
        self.handler_calls.append(job.id)
        if job.error_message == "trigger_failure":
            raise ValueError("Mock error triggered")

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)

    def test_claim_and_execute_successful_job(self):
        # 1. Add a queued job
        job = IngestionJob(
            id="job-1",
            run_id=self.run_id,
            document_id=self.doc_id,
            job_type="mock_task",
            status="queued"
        )
        self.session.add(job)
        self.session.commit()

        # 2. Claim and process
        claimed_id = self.worker.claim_job()
        self.assertEqual(claimed_id, "job-1")
        
        # Verify job was leased
        leased_job = self.session.query(IngestionJob).filter_by(id="job-1").first()
        self.assertEqual(leased_job.status, "leased")
        self.assertEqual(leased_job.attempts, 1)
        self.assertIsNotNone(leased_job.lease_until)

        # Process job
        success = self.worker.process_job(claimed_id)
        self.assertTrue(success)

        # Verify job succeeded
        self.session.expire_all()
        done_job = self.session.query(IngestionJob).filter_by(id="job-1").first()
        self.assertEqual(done_job.status, "succeeded")
        self.assertIsNone(done_job.lease_until)
        self.assertIsNone(done_job.error_message)
        self.assertEqual(self.handler_calls, ["job-1"])

    def test_job_failure_and_retry_backoff(self):
        # 1. Add a job configured to trigger error
        job = IngestionJob(
            id="job-fail",
            run_id=self.run_id,
            document_id=self.doc_id,
            job_type="mock_task",
            status="queued",
            error_message="trigger_failure"
        )
        self.session.add(job)
        self.session.commit()

        # 2. Claim and process (1st failure)
        claimed_id = self.worker.claim_job()
        success = self.worker.process_job(claimed_id)
        self.assertFalse(success)

        # Verify job status updated to failed with attempt=1 and backoff set
        self.session.expire_all()
        failed_job = self.session.query(IngestionJob).filter_by(id="job-fail").first()
        self.assertEqual(failed_job.status, "failed")
        self.assertEqual(failed_job.attempts, 1)
        self.assertIn("Mock error triggered", failed_job.error_message)
        
        # Backoff: base (5s) * (2^(attempts-1)) = 5s
        expected_time_diff = datetime.timedelta(seconds=5)
        now = datetime.datetime.utcnow()
        self.assertAlmostEqual(
            (failed_job.next_attempt_at - now).total_seconds(), 
            expected_time_diff.total_seconds(), 
            delta=2
        )

    def test_job_max_attempts_cap(self):
        # Add a job that already failed max_attempts (3) times
        job = IngestionJob(
            id="job-exhausted",
            run_id=self.run_id,
            document_id=self.doc_id,
            job_type="mock_task",
            status="failed",
            attempts=3,
            next_attempt_at=datetime.datetime.utcnow() - datetime.timedelta(seconds=1)
        )
        self.session.add(job)
        self.session.commit()

        # Try to claim
        claimed_id = self.worker.claim_job()
        self.assertIsNone(claimed_id)

    def test_claim_expired_lease(self):
        # Add a job leased in the past (lease expired)
        past_time = datetime.datetime.utcnow() - datetime.timedelta(minutes=10)
        job = IngestionJob(
            id="job-expired-lease",
            run_id=self.run_id,
            document_id=self.doc_id,
            job_type="mock_task",
            status="leased",
            lease_until=past_time
        )
        self.session.add(job)
        self.session.commit()

        # Claim the expired lease
        claimed_id = self.worker.claim_job()
        self.assertEqual(claimed_id, "job-expired-lease")


if __name__ == "__main__":
    unittest.main()
