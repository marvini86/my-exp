from datetime import timedelta
from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session

from core.account_context import AccountContext, get_account_context
from core.config import settings
from core.exceptions import PayloadTooLargeError
from core.rate_limit import DOCUMENT_UPLOAD, rate_limit
from database.connection import get_db
from models.document import ProcessingStatus
from repositories.document import DocumentRepository, IngestionJobRepository
from repositories.financial_entry import FinancialEntryRepository
from repositories.vector_store import VectorStore
from schemas.document import DocumentDetailResponse, DocumentDownloadResponse, DocumentPage, DocumentResponse
from services.blob_storage import BlobStorage, get_blob_storage
from services.document import DocumentService
from services.search import get_vector_store

router = APIRouter(prefix="/accounts/{account_id}/documents", tags=["Documents"])

# Room for the multipart headers on top of the file itself.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024


def get_document_service(
    session: Session = Depends(get_db),
    storage: BlobStorage = Depends(get_blob_storage),
) -> DocumentService:
    return DocumentService(
        session,
        DocumentRepository(session),
        IngestionJobRepository(session),
        storage,
        max_upload_bytes=settings.upload.max_bytes,
        download_url_ttl=timedelta(seconds=settings.storage.download_url_ttl_seconds),
    )


def reject_oversized_request(request: Request) -> None:
    """Rejects early based on the declared Content-Length.

    Partial protection: the definitive body limit belongs in the reverse
    proxy (e.g. `client_max_body_size` in nginx).
    """
    content_length = request.headers.get("content-length")
    limit = settings.upload.max_bytes + _MULTIPART_OVERHEAD_BYTES

    if content_length and content_length.isdigit() and int(content_length) > limit:
        raise PayloadTooLargeError(f"File exceeds the maximum size of {settings.upload.max_bytes} bytes")


@router.post(
    "/",
    response_model=DocumentResponse,
    status_code=HTTPStatus.CREATED,
    dependencies=[Depends(reject_oversized_request), Depends(rate_limit(DOCUMENT_UPLOAD))],
)
def upload_document(
    file: UploadFile = File(..., description="PDF, JPG, PNG ou XML"),
    context: AccountContext = Depends(get_account_context),
    service: DocumentService = Depends(get_document_service),
) -> DocumentResponse:
    return service.upload(context, file.file, file.filename)


@router.get("/", response_model=DocumentPage)
def list_documents(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: ProcessingStatus | None = None,
    context: AccountContext = Depends(get_account_context),
    service: DocumentService = Depends(get_document_service),
) -> DocumentPage:
    """Mais recentes primeiro. `status` filtra (ex.: `failed`, `pending`)."""
    return service.list_documents(context, limit=limit, offset=offset, status=status)


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: UUID,
    context: AccountContext = Depends(get_account_context),
    service: DocumentService = Depends(get_document_service),
    session: Session = Depends(get_db),
) -> DocumentDetailResponse:
    """Document with the extracted entries and items."""
    return service.get_document(context, document_id, FinancialEntryRepository(session))


@router.post("/{document_id}/reprocess", response_model=DocumentResponse, status_code=HTTPStatus.ACCEPTED)
def reprocess_document(
    document_id: UUID,
    context: AccountContext = Depends(get_account_context),
    service: DocumentService = Depends(get_document_service),
) -> DocumentResponse:
    """Puts the document back in the extraction queue (e.g. it failed or the extraction improved)."""
    return service.reprocess(context, document_id)


@router.delete("/{document_id}", status_code=HTTPStatus.NO_CONTENT)
def delete_document(
    document_id: UUID,
    context: AccountContext = Depends(get_account_context),
    service: DocumentService = Depends(get_document_service),
    store: VectorStore = Depends(get_vector_store),
) -> Response:
    """Deletes the document, the file, the entries/items and the index. Uploader, owner or admin."""
    service.delete(context, document_id, store)
    return Response(status_code=HTTPStatus.NO_CONTENT)


@router.get("/{document_id}/download", response_model=DocumentDownloadResponse)
def get_download_url(
    document_id: UUID,
    context: AccountContext = Depends(get_account_context),
    service: DocumentService = Depends(get_document_service),
) -> DocumentDownloadResponse:
    """Temporary URL (SAS, read-only) to download the original file."""
    return service.get_download_url(context, document_id)
