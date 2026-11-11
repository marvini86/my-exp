from typing import Protocol

from schemas.extraction import ExtractedDocument


class Extractor(Protocol):
    """Turns a file's content into the `ExtractedDocument` contract.

    Must raise `PermanentProcessingError` when the document cannot be
    interpreted (a retry would not help) and let other exceptions
    (e.g. network) propagate so the worker tries again.
    """

    def supports(self, mime_type: str) -> bool: ...

    def extract(self, content: bytes, mime_type: str) -> ExtractedDocument: ...
