import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(StrEnum):
    INGEST = "ingest"  # financial extraction
    INDEX = "index"  # indexing for semantic search


def _status_column(enum_cls: type[StrEnum] = ProcessingStatus) -> Enum:
    # VARCHAR + CHECK in the database; no native PostgreSQL enum.
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=30,
        values_callable=lambda statuses: [status.value for status in statuses],
    )


class Document(UUIDPrimaryKey, Timestamped, Base):
    """File uploaded by the user. RLS-protected table."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("account_id", "id", name="uq_documents_account_id"),)

    account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id"))

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(150))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    sha256: Mapped[str | None] = mapped_column(String(64))
    blob_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    document_type: Mapped[str | None] = mapped_column(String(100))
    extracted_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    processing_status: Mapped[ProcessingStatus] = mapped_column(
        _status_column(),
        nullable=False,
        default=ProcessingStatus.PENDING,
        server_default=ProcessingStatus.PENDING.value,
    )
    processing_error: Mapped[str | None] = mapped_column(Text)
    indexed_at: Mapped[datetime | None] = mapped_column()


class IngestionJob(UUIDPrimaryKey, Timestamped, Base):
    """Persistent document processing queue. RLS-protected table."""

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "document_id"],
            ["documents.account_id", "documents.id"],
            name="fk_ingestion_document",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    job_type: Mapped[JobType] = mapped_column(
        _status_column(JobType),
        nullable=False,
        default=JobType.INGEST,
        server_default=JobType.INGEST.value,
    )
    status: Mapped[ProcessingStatus] = mapped_column(
        _status_column(),
        nullable=False,
        default=ProcessingStatus.PENDING,
        server_default=ProcessingStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default=text("3"))
    available_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    locked_at: Mapped[datetime | None] = mapped_column()
    locked_by: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
