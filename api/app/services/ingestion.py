"""Lifecycle of an ingestion job (run by the worker).

Each step uses its own short transaction; the file download happens
outside a transaction, so no connections or locks are held during external I/O.

    claim (commit) → start (commit) → download → complete (commit)
                                         └─ failure → retry with backoff | failed
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from core.config import WorkerSettings
from core.exceptions import NotFoundError
from database.tenant_context import set_worker_context
from models.document import Document, JobType, ProcessingStatus
from repositories.document import ClaimedJob, DocumentRepository, IngestionJobRepository
from services.blob_storage import BlobStorage
from services.document_processor import DeferProcessingError, DocumentProcessor, PermanentProcessingError

logger = logging.getLogger(__name__)

_MAX_ERROR_LENGTH = 2000

# Errors where retrying does not help.
_PERMANENT_ERRORS = (PermanentProcessingError, NotFoundError)


class IngestionService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        storage: BlobStorage,
        processor: DocumentProcessor,
        worker_id: str,
        config: WorkerSettings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        index_processor: DocumentProcessor | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.storage = storage
        self.processors: dict[JobType, DocumentProcessor | None] = {
            JobType.INGEST: processor,
            JobType.INDEX: index_processor,
        }
        self.worker_id = worker_id
        self.config = config
        self.clock = clock

    @staticmethod
    def _tracks_document_status(job: ClaimedJob) -> bool:
        # Only extraction defines the document status; indexing is secondary.
        return job.job_type is JobType.INGEST

    def process_next(self) -> bool:
        """Processes one job, if any. Returns False when the queue is empty."""
        job = self._claim()

        if job is None:
            return False

        logger.info(
            "Job claimed | job=%s type=%s document=%s attempt=%s/%s",
            job.job_id,
            job.job_type,
            job.document_id,
            job.attempts,
            job.max_attempts,
        )

        if job.attempts > job.max_attempts:
            # Recovered from a worker that crashed repeatedly during processing.
            self._record_failure(job, "Maximum attempts exceeded (processing was interrupted)", retry=False)
            return True

        try:
            blob_path = self._start(job)
            content = self.storage.download(blob_path)
            self._complete(job, content)
        except DeferProcessingError as exc:
            self._defer(job, self._describe(exc), exc.delay_seconds)
        except _PERMANENT_ERRORS as exc:
            self._record_failure(job, self._describe(exc), retry=False)
        except Exception as exc:
            logger.exception("Job attempt failed | job=%s", job.job_id)
            self._record_failure(job, self._describe(exc), retry=job.attempts < job.max_attempts)

        return True

    def _claim(self) -> ClaimedJob | None:
        with self.session_factory() as session:
            job = IngestionJobRepository(session).claim_next(
                self.worker_id,
                timedelta(seconds=self.config.lock_timeout_seconds),
            )
            session.commit()
            return job

    def _start(self, job: ClaimedJob) -> str:
        with self.session_factory() as session:
            set_worker_context(session, job.job_id, self.worker_id)
            document = self._get_document(session, job)

            if self._tracks_document_status(job):
                document.processing_status = ProcessingStatus.PROCESSING
                document.processing_error = None
            blob_path = document.blob_path

            session.commit()
            return blob_path

    def _complete(self, job: ClaimedJob, content: bytes) -> None:
        with self.session_factory() as session:
            set_worker_context(session, job.job_id, self.worker_id)

            owned = IngestionJobRepository(session).get_owned(job.job_id, self.worker_id)
            if owned is None:
                logger.warning("Job lock lost before completion; skipping | job=%s", job.job_id)
                return

            processor = self.processors.get(job.job_type)
            if processor is None:
                raise PermanentProcessingError(f"No processor configured for job type '{job.job_type}'")

            document = self._get_document(session, job)
            processor.process(session, document, content)

            if self._tracks_document_status(job):
                document.processing_status = ProcessingStatus.COMPLETED
                document.processing_error = None

            owned.status = ProcessingStatus.COMPLETED
            owned.locked_at = None
            owned.locked_by = None
            owned.last_error = None

            session.commit()

        logger.info("Job completed | job=%s document=%s", job.job_id, job.document_id)

    def _record_failure(self, job: ClaimedJob, message: str, *, retry: bool) -> None:
        try:
            with self.session_factory() as session:
                set_worker_context(session, job.job_id, self.worker_id)

                owned = IngestionJobRepository(session).get_owned(job.job_id, self.worker_id)
                if owned is None:
                    logger.warning("Job lock lost before recording failure | job=%s", job.job_id)
                    return

                document = (
                    DocumentRepository(session).get(job.account_id, job.document_id)
                    if self._tracks_document_status(job)
                    else None
                )

                owned.locked_at = None
                owned.locked_by = None
                owned.last_error = message

                if retry:
                    owned.status = ProcessingStatus.PENDING
                    owned.available_at = self.clock() + self._backoff(job.attempts)
                    if document is not None:
                        document.processing_status = ProcessingStatus.PENDING
                else:
                    owned.status = ProcessingStatus.FAILED
                    if document is not None:
                        document.processing_status = ProcessingStatus.FAILED
                        document.processing_error = message

                session.commit()
        except Exception:
            # If not even recording the failure works, the job stays "processing" and is
            # recovered by the lock timeout.
            logger.exception("Could not record job failure | job=%s", job.job_id)
            return

        logger.warning(
            "Job %s | job=%s attempt=%s/%s error=%s",
            "will retry" if retry else "failed",
            job.job_id,
            job.attempts,
            job.max_attempts,
            message,
        )

    def _defer(self, job: ClaimedJob, message: str, delay_seconds: float) -> None:
        """Returns the job to the queue without using an attempt (external resource exhausted)."""
        try:
            with self.session_factory() as session:
                set_worker_context(session, job.job_id, self.worker_id)

                owned = IngestionJobRepository(session).get_owned(job.job_id, self.worker_id)
                if owned is None:
                    logger.warning("Job lock lost before deferring | job=%s", job.job_id)
                    return

                owned.status = ProcessingStatus.PENDING
                owned.attempts = max(owned.attempts - 1, 0)
                owned.available_at = self.clock() + timedelta(seconds=delay_seconds)
                owned.locked_at = None
                owned.locked_by = None
                owned.last_error = message

                if self._tracks_document_status(job):
                    document = DocumentRepository(session).get(job.account_id, job.document_id)
                    if document is not None:
                        document.processing_status = ProcessingStatus.PENDING

                session.commit()
        except Exception:
            logger.exception("Could not defer job | job=%s", job.job_id)
            return

        logger.warning("Job deferred | job=%s delay=%ss reason=%s", job.job_id, int(delay_seconds), message)

    def _backoff(self, attempts: int) -> timedelta:
        return timedelta(seconds=self.config.retry_base_seconds * self.config.retry_factor ** max(attempts - 1, 0))

    @staticmethod
    def _get_document(session: Session, job: ClaimedJob) -> Document:
        document = DocumentRepository(session).get(job.account_id, job.document_id)

        if document is None:
            raise PermanentProcessingError("Document not found")

        return document

    @staticmethod
    def _describe(exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}"[:_MAX_ERROR_LENGTH]
