from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from models.document import ProcessingStatus
from schemas.financial import EntryResponse


class DocumentResponse(BaseModel):
    id: UUID
    original_filename: str
    mime_type: str | None
    file_size: int | None
    document_type: str | None
    processing_status: ProcessingStatus
    # Set when the document can already be found by semantic search.
    indexed_at: datetime | None
    uploaded_by: UUID | None
    created_at: datetime
    updated_at: datetime
    # Postponed extraction (e.g. free AI daily limit): when the worker retries.
    next_attempt_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DocumentDownloadResponse(BaseModel):
    url: str
    expires_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int


class DocumentItemResponse(BaseModel):
    id: UUID
    description: str
    brand: str | None
    quantity: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    total_price: Decimal | None

    model_config = ConfigDict(from_attributes=True)


class DocumentDetailResponse(DocumentResponse):
    """Document + what was extracted from it.

    The internal processing error is not exposed (it may contain provider/infra
    details); `processing_status=failed` is enough for the UI.
    """

    entries: list[EntryResponse]
    items: list[DocumentItemResponse]
