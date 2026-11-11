import hashlib
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import BinaryIO
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.account_context import AccountContext
from core.exceptions import (
    AppError,
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from models.account_member import AccountRole
from models.document import Document, IngestionJob, ProcessingStatus
from repositories.document import DocumentRepository, IngestionJobRepository
from repositories.financial_entry import EntryFilters, FinancialEntryRepository
from repositories.vector_store import VectorStore
from schemas.document import (
    DocumentDetailResponse,
    DocumentDownloadResponse,
    DocumentItemResponse,
    DocumentPage,
    DocumentResponse,
)
from services.account_access import require_role
from services.blob_storage import BlobStorage
from services.financial import entry_response
from services.file_type import SNIFF_BYTES, XML, detect_file_type

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 64 * 1024
_MAX_FILENAME_LENGTH = 255
_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class DocumentService:
    def __init__(
        self,
        session: Session,
        document_repository: DocumentRepository,
        job_repository: IngestionJobRepository,
        storage: BlobStorage,
        *,
        max_upload_bytes: int,
        download_url_ttl: timedelta,
    ) -> None:
        self.session = session
        self.document_repository = document_repository
        self.job_repository = job_repository
        self.storage = storage
        self.max_upload_bytes = max_upload_bytes
        self.download_url_ttl = download_url_ttl

    def list_documents(
        self,
        context: AccountContext,
        *,
        limit: int,
        offset: int,
        status: ProcessingStatus | None = None,
    ) -> DocumentPage:
        documents, total = self.document_repository.list_by_account(
            context.account_id, limit=limit, offset=offset, status=status
        )
        return DocumentPage(items=self._responses(context, documents), total=total, limit=limit, offset=offset)

    def _responses(self, context: AccountContext, documents: list[Document]) -> list[DocumentResponse]:
        waiting = self.job_repository.next_attempts(context.account_id, [d.id for d in documents])
        return [
            DocumentResponse.model_validate(d).model_copy(update={"next_attempt_at": waiting.get(d.id)})
            for d in documents
        ]

    def get_document(
        self, context: AccountContext, document_id: UUID, entry_repository: FinancialEntryRepository
    ) -> DocumentDetailResponse:
        document = self._get_or_404(context, document_id)
        # A document produces few entries (usually 1); 100 is just a ceiling.
        entries, _ = entry_repository.list_page(
            context.account_id, EntryFilters(document_id=document.id), limit=100, offset=0
        )
        return DocumentDetailResponse.model_validate(
            {
                **self._responses(context, [document])[0].model_dump(),
                "entries": [entry_response(row) for row in entries],
                "items": [
                    DocumentItemResponse.model_validate(item)
                    for item in self.document_repository.items(context.account_id, document.id)
                ],
            }
        )

    def get_download_url(self, context: AccountContext, document_id: UUID) -> DocumentDownloadResponse:
        document = self._get_or_404(context, document_id)
        expires_at = datetime.now(UTC) + self.download_url_ttl

        return DocumentDownloadResponse(
            url=self.storage.generate_read_url(document.blob_path, expires_at),
            expires_at=expires_at,
        )

    def upload(self, context: AccountContext, stream: BinaryIO, filename: str | None) -> DocumentResponse:
        """Validates, stores in Blob and registers the document + ingestion job.

        Any account member can upload documents.
        """
        sha256, size = self._hash_and_measure(stream)

        stream.seek(0)
        file_type = detect_file_type(stream.read(SNIFF_BYTES))
        if file_type is None:
            raise UnsupportedMediaTypeError("Unsupported file type. Allowed: PDF, JPG, PNG, XML")

        if self.document_repository.get_by_sha256(context.account_id, sha256) is not None:
            raise ConflictError("Document already uploaded")

        document_id = uuid.uuid4()
        key = f"expenses/{context.account_id}/documents/{document_id}.{file_type.extension}"

        stream.seek(0)
        blob_path = self.storage.upload(
            key,
            stream,
            file_type.mime_type,
            # XML could be rendered by the browser; force a download.
            as_attachment=file_type is XML,
        )

        document = Document(
            id=document_id,
            account_id=context.account_id,
            uploaded_by=context.user.id,
            original_filename=self._sanitize_filename(filename),
            mime_type=file_type.mime_type,
            file_size=size,
            sha256=sha256,
            blob_path=blob_path,
        )

        try:
            self.document_repository.add(document)
            self.job_repository.add(IngestionJob(account_id=context.account_id, document_id=document_id))
            self.session.commit()
        except Exception as exc:
            self.session.rollback()
            self._discard_blob(blob_path)
            if isinstance(exc, IntegrityError):
                # Race with a simultaneous upload of the same file (uq_documents_hash).
                raise ConflictError("Document already uploaded") from exc
            raise

        logger.info(
            "Document uploaded | account=%s document=%s type=%s size=%s",
            context.account_id,
            document_id,
            file_type.mime_type,
            size,
        )
        return DocumentResponse.model_validate(document)

    def reprocess(self, context: AccountContext, document_id: UUID) -> DocumentResponse:
        """Extracts again (it failed, or the extraction improved). Any member can."""
        document = self._get_or_404(context, document_id)
        if self.job_repository.has_active_ingest(context.account_id, document.id):
            raise ConflictError("Document is already queued for processing")

        self.job_repository.add(IngestionJob(account_id=context.account_id, document_id=document.id))
        document.processing_status = ProcessingStatus.PENDING
        document.processing_error = None
        self.session.commit()
        logger.info("Document reprocess requested | document=%s", document.id)
        return self._responses(context, [document])[0]

    def delete(self, context: AccountContext, document_id: UUID, vector_store: VectorStore | None) -> None:
        """Deletes the document and what came from it. Uploader, owner or account admin."""
        document = self._get_or_404(context, document_id)
        if document.uploaded_by != context.user.id:
            require_role(context.role, AccountRole.OWNER, AccountRole.ADMIN)
        if document.processing_status == ProcessingStatus.PROCESSING:
            # The worker is holding it: deleting now would orphan the job halfway.
            raise ConflictError("Document is being processed, try again in a moment")

        blob_path = document.blob_path
        self.document_repository.delete_with_extraction(context.account_id, document.id)
        self.session.commit()
        logger.info("Document deleted | document=%s", document.id)

        # After the commit, best effort: the database is the source of truth.
        try:
            self.storage.delete(blob_path)
        except Exception:
            logger.exception("Could not delete blob of deleted document | document=%s", document.id)
        if vector_store is not None:
            try:
                vector_store.delete_document(context.account_id, document.id)
            except Exception:
                logger.exception("Could not delete search index of deleted document | document=%s", document.id)

    def _get_or_404(self, context: AccountContext, document_id: UUID) -> Document:
        document = self.document_repository.get(context.account_id, document_id)

        if document is None:
            raise NotFoundError("Document not found")

        return document

    def _hash_and_measure(self, stream: BinaryIO) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0

        stream.seek(0)
        while chunk := stream.read(_CHUNK_SIZE):
            size += len(chunk)
            if size > self.max_upload_bytes:
                raise PayloadTooLargeError(f"File exceeds the maximum size of {self.max_upload_bytes} bytes")
            digest.update(chunk)

        if size == 0:
            raise AppError("Empty file")

        return digest.hexdigest(), size

    def _discard_blob(self, blob_path: str) -> None:
        try:
            self.storage.delete(blob_path)
        except Exception:
            # Does not mask the original error; the orphan blob is recorded in the log.
            logger.exception("Failed to remove orphan blob | blob=%s", blob_path)

    @staticmethod
    def _sanitize_filename(filename: str | None) -> str:
        """Display only: never used to build paths."""
        name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        name = _UNSAFE_FILENAME_CHARS.sub("", name).strip()
        return name[:_MAX_FILENAME_LENGTH] or "document"
