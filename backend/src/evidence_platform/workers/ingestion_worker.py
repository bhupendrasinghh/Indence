import time
import logging
import datetime
from typing import Any, Callable
from sqlalchemy.orm import Session
from sqlalchemy import select, and_, or_

from ..db.models.models import IngestionJob, IngestionRun

logger = logging.getLogger("evidence_platform.worker")

class IngestionWorker:
    """Worker daemon that claims and executes background ingestion jobs."""

    def __init__(
        self,
        db_session_factory: Callable[[], Session],
        max_attempts: int = 5,
        lease_duration_seconds: int = 300,
        backoff_base_delay: int = 10,
    ) -> None:
        self.session_factory = db_session_factory
        self.max_attempts = max_attempts
        self.lease_duration = lease_duration_seconds
        self.backoff_base = backoff_base_delay
        self.job_handlers: dict[str, Callable[[IngestionJob, Session], None]] = {}

    def register_handler(self, job_type: str, handler: Callable[[IngestionJob, Session], None]) -> None:
        """Register a handler function for a specific job_type."""
        self.job_handlers[job_type] = handler

    def claim_job(self) -> IngestionJob | None:
        """Atomically claim a single pending job using locks if supported.
        
        Transitions job state to 'leased' and returns it.
        """
        session = self.session_factory()
        try:
            now = datetime.datetime.utcnow()
            
            # Query for jobs that are:
            # - 'queued'
            # - OR 'failed' but have remaining attempts and next_attempt_at is in the past
            # - OR 'leased' but lease has expired (stuck jobs)
            query = session.query(IngestionJob).filter(
                or_(
                    IngestionJob.status == "queued",
                    and_(
                        IngestionJob.status == "failed",
                        IngestionJob.attempts < self.max_attempts,
                        IngestionJob.next_attempt_at <= now
                    ),
                    and_(
                        IngestionJob.status == "leased",
                        IngestionJob.lease_until <= now
                    )
                )
            ).order_by(IngestionJob.next_attempt_at)

            # Apply SELECT FOR UPDATE SKIP LOCKED on PostgreSQL
            if session.bind.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)

            job = query.first()
            if not job:
                return None

            # Atomically lease the job
            job.status = "leased"
            job.attempts += 1
            job.lease_until = now + datetime.timedelta(seconds=self.lease_duration)
            session.commit()
            
            # Evict job from session to allow caller to process it in their own session
            # (or we return the ID and let them load it)
            job_id = job.id
            return job_id
        except Exception as e:
            logger.error(f"Error claiming job: {e}")
            session.rollback()
            return None
        finally:
            session.close()

    def claim_batch_jobs(self, limit: int = 10) -> list[str]:
        """Claim up to `limit` pending jobs atomically."""
        session = self.session_factory()
        try:
            now = datetime.datetime.utcnow()
            query = session.query(IngestionJob).filter(
                or_(
                    IngestionJob.status == "queued",
                    and_(
                        IngestionJob.status == "failed",
                        IngestionJob.attempts < self.max_attempts,
                        IngestionJob.next_attempt_at <= now
                    ),
                    and_(
                        IngestionJob.status == "leased",
                        IngestionJob.lease_until <= now
                    )
                )
            ).order_by(IngestionJob.next_attempt_at).limit(limit)

            jobs = query.all()
            if not jobs:
                return []

            job_ids = []
            for job in jobs:
                job.status = "leased"
                job.attempts += 1
                job.lease_until = now + datetime.timedelta(seconds=self.lease_duration)
                job_ids.append(job.id)
            
            session.commit()
            return job_ids
        except Exception as e:
            logger.error(f"Error claiming batch jobs: {e}")
            session.rollback()
            return []
        finally:
            session.close()

    def process_job(self, job_id: str) -> bool:
        """Execute the job handler and transition state to succeeded/failed."""
        session = self.session_factory()
        job = session.query(IngestionJob).filter_by(id=job_id).first()
        if not job:
            session.close()
            return False

        logger.info(f"Processing job {job.id} (type: {job.job_type}, document: {job.document_id})")
        handler = self.job_handlers.get(job.job_type)
        
        try:
            if not handler:
                raise ValueError(f"No handler registered for job type: {job.job_type}")
            
            # Execute handler
            handler(job, session)
            
            # Transition to success
            job.status = "succeeded"
            job.lease_until = None
            job.error_message = None
            session.commit()
            logger.info(f"Job {job.id} completed successfully.")
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Job {job.id} failed: {e}")
            
            # Calculate backoff delay: base * (2 ^ (attempts - 1))
            now = datetime.datetime.utcnow()
            delay = self.backoff_base * (2 ** (job.attempts - 1))
            
            job.status = "failed"
            job.lease_until = None
            job.next_attempt_at = now + datetime.timedelta(seconds=delay)
            job.error_message = str(e)
            session.commit()
            return False
        finally:
            session.close()

    def process_batch_jobs(self, job_ids: list[str]) -> bool:
        """Execute batch job handler for a list of job IDs."""
        if not job_ids:
            return True

        session = self.session_factory()
        jobs = session.query(IngestionJob).filter(IngestionJob.id.in_(job_ids)).all()
        if not jobs:
            session.close()
            return False

        try:
            if hasattr(self, "index_batch_jobs_handler"):
                self.index_batch_jobs_handler(jobs, session)
            else:
                for job in jobs:
                    handler = self.job_handlers.get(job.job_type)
                    if handler:
                        handler(job, session)
            
            # Mark all jobs as succeeded in SQLite
            session.query(IngestionJob).filter(IngestionJob.id.in_(job_ids)).update(
                {"status": "succeeded", "lease_until": None, "error_message": None},
                synchronize_session=False
            )
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.error(f"Batch jobs failed: {e}")
            now = datetime.datetime.utcnow()
            session.query(IngestionJob).filter(IngestionJob.id.in_(job_ids)).update(
                {"status": "failed", "lease_until": None, "error_message": str(e)},
                synchronize_session=False
            )
            session.commit()
            return False
        finally:
            session.close()

    def run_once(self) -> bool:
        """Claim and run a single job. Returns True if a job was processed."""
        job_id = self.claim_job()
        if job_id:
            self.process_job(job_id)
            return True
        return False
