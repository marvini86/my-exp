from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.orm import Session

from models.document import Document, IngestionJob, JobType, ProcessingStatus
from models.financial import DocumentItem, FinancialEntry


class DocumentRepository:
    """Document access. Filters by `account_id` on top of RLS."""

    def __init__(self, session: Session):
        self.session = session

    def get(self, account_id: UUID, document_id: UUID) -> Document | None:
        stmt = select(Document).where(
            Document.account_id == account_id,
            Document.id == document_id,
        )
        return self.session.scalars(stmt).one_or_none()

    def get_many(self, account_id: UUID, document_ids: list[UUID]) -> dict[UUID, Document]:
        if not document_ids:
            return {}
        stmt = select(Document).where(
            Document.account_id == account_id,
            Document.id.in_(document_ids),
        )
        return {document.id: document for document in self.session.scalars(stmt).all()}

    def get_by_sha256(self, account_id: UUID, sha256: str) -> Document | None:
        stmt = select(Document).where(
            Document.account_id == account_id,
            Document.sha256 == sha256,
        )
        return self.session.scalars(stmt).one_or_none()

    def list_by_account(
        self, account_id: UUID, *, limit: int, offset: int, status: ProcessingStatus | None = None
    ) -> tuple[list[Document], int]:
        conditions = [Document.account_id == account_id]
        if status is not None:
            conditions.append(Document.processing_status == status)
        total = self.session.scalar(select(func.count()).select_from(Document).where(*conditions)) or 0
        stmt = (
            select(Document)
            .where(*conditions)
            .order_by(Document.created_at.desc(), Document.id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt).all()), total

    def delete_with_extraction(self, account_id: UUID, document_id: UUID) -> None:
        """Removes the document and everything derived from it (items, entries, jobs)."""
        for model, column in (
            (DocumentItem, DocumentItem.document_id),
            (FinancialEntry, FinancialEntry.document_id),
            (IngestionJob, IngestionJob.document_id),
            (Document, Document.id),
        ):
            self.session.execute(delete(model).where(model.account_id == account_id, column == document_id))

    def items(self, account_id: UUID, document_id: UUID) -> list[DocumentItem]:
        stmt = (
            select(DocumentItem)
            .where(DocumentItem.account_id == account_id, DocumentItem.document_id == document_id)
            .order_by(DocumentItem.created_at, DocumentItem.description)
        )
        return list(self.session.scalars(stmt).all())

    def add(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document


@dataclass(frozen=True)
class ClaimedJob:
    job_id: UUID
    account_id: UUID
    document_id: UUID
    job_type: JobType
    attempts: int
    max_attempts: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_type", JobType(self.job_type))


class IngestionJobRepository:
    def __init__(self, session: Session):
        self.session = session

    def next_attempts(self, account_id: UUID, document_ids: list[UUID]) -> dict[UUID, datetime]:
        """Documents whose extraction was postponed (e.g. AI out of quota): when it retries."""
        if not document_ids:
            return {}
        stmt = (
            select(IngestionJob.document_id, func.min(IngestionJob.available_at))
            .where(
                IngestionJob.account_id == account_id,
                IngestionJob.document_id.in_(document_ids),
                IngestionJob.job_type == JobType.INGEST,
                IngestionJob.status == ProcessingStatus.PENDING,
                IngestionJob.available_at > func.now(),
            )
            .group_by(IngestionJob.document_id)
        )
        return dict(self.session.execute(stmt).all())

    def has_active_ingest(self, account_id: UUID, document_id: UUID) -> bool:
        stmt = select(
            exists().where(
                IngestionJob.account_id == account_id,
                IngestionJob.document_id == document_id,
                IngestionJob.job_type == JobType.INGEST,
                IngestionJob.status.in_([ProcessingStatus.PENDING, ProcessingStatus.PROCESSING]),
            )
        )
        return bool(self.session.scalar(stmt))

    def add(self, job: IngestionJob) -> IngestionJob:
        self.session.add(job)
        self.session.flush()
        return job

    def claim_next(self, worker_id: str, lock_timeout: timedelta) -> ClaimedJob | None:
        """Takes the next available job (database function; role myexp_worker)."""
        row = self.session.execute(
            text("SELECT * FROM app_claim_ingestion_job(:worker_id, :lock_timeout)"),
            {"worker_id": worker_id, "lock_timeout": lock_timeout},
        ).one_or_none()

        return ClaimedJob(*row) if row is not None else None

    def get_owned(self, job_id: UUID, worker_id: str) -> IngestionJob | None:
        """Job still being processed by this worker, locked for update."""
        stmt = (
            select(IngestionJob)
            .where(
                IngestionJob.id == job_id,
                IngestionJob.status == ProcessingStatus.PROCESSING,
                IngestionJob.locked_by == worker_id,
            )
            .with_for_update()
        )
        return self.session.scalars(stmt).one_or_none()
