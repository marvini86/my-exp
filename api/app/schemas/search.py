from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SearchResultResponse(BaseModel):
    document_id: UUID
    original_filename: str
    document_type: str | None
    document_created_at: datetime
    kind: str  # "summary" | "text"
    content: str
    score: float
    # GET /accounts/{account_id}/documents/{document_id}/download
    download_path: str


class SearchResponse(BaseModel):
    query: str
    model: str
    results: list[SearchResultResponse]
