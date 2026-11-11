"""Processing of a document already downloaded from storage.

Extension point for financial extraction: a processor receives the
session (with the account context active), the document and the file content,
and persists what it extracts. Everything happens in the same transaction that marks the
document as completed.
"""

import hashlib
from typing import Protocol

from sqlalchemy.orm import Session

from models.document import Document


class PermanentProcessingError(Exception):
    """Failure that a retry will not fix (e.g. corrupted file)."""


class DeferProcessingError(Exception):
    """External resource temporarily exhausted (e.g. free model limits).

    The job goes back to the queue after `delay_seconds` WITHOUT using an attempt:
    it is not the document's fault.
    """

    def __init__(self, message: str, delay_seconds: float) -> None:
        super().__init__(message)
        self.delay_seconds = delay_seconds


class DocumentProcessor(Protocol):
    def process(self, session: Session, document: Document, content: bytes) -> None: ...


class IntegrityCheckProcessor:
    """Initial processor: ensures the stored file is the one that was uploaded."""

    def process(self, session: Session, document: Document, content: bytes) -> None:
        if document.sha256 and hashlib.sha256(content).hexdigest() != document.sha256:
            raise PermanentProcessingError("Stored file checksum does not match the uploaded file")
