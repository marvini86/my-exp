"""Semantic search over the account's documents."""

import logging
from functools import lru_cache

from qdrant_client import QdrantClient

from core.account_context import AccountContext
from core.config import settings
from core.exceptions import ServiceUnavailableError
from repositories.document import DocumentRepository
from repositories.vector_store import VectorStore
from schemas.search import SearchResponse, SearchResultResponse
from services.embeddings import EmbeddingRouter, EmbeddingUnavailableError

logger = logging.getLogger(__name__)


@lru_cache
def get_embedding_router() -> EmbeddingRouter:
    """One router per process (keeps cooldowns and HTTP connections)."""
    return EmbeddingRouter.from_settings(settings.openrouter, settings.embedding, settings.providers)


@lru_cache
def get_vector_store() -> VectorStore:
    client = QdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None, timeout=10)
    return VectorStore(client, settings.qdrant.collection)


class SearchService:
    def __init__(
        self,
        document_repository: DocumentRepository,
        embeddings: EmbeddingRouter,
        store: VectorStore,
    ) -> None:
        self.document_repository = document_repository
        self.embeddings = embeddings
        self.store = store

    def search(
        self,
        context: AccountContext,
        query: str,
        *,
        limit: int,
        document_type: str | None = None,
    ) -> SearchResponse:
        try:
            vector_name, vector = self.embeddings.embed_query(query)
        except EmbeddingUnavailableError as exc:
            logger.warning("Semantic search unavailable | %s", exc)
            raise ServiceUnavailableError("Semantic search temporarily unavailable") from exc

        # account_id is required: the search never leaves the context account.
        hits = self.store.search(context.account_id, vector_name, vector, limit=limit, document_type=document_type)

        # Confirms in PostgreSQL (under RLS) and drops documents that do not exist.
        documents = self.document_repository.get_many(context.account_id, list({hit.document_id for hit in hits}))

        results = [
            SearchResultResponse(
                document_id=hit.document_id,
                original_filename=document.original_filename,
                document_type=document.document_type,
                document_created_at=document.created_at,
                kind=hit.kind,
                content=hit.content,
                score=round(hit.score, 4),
                download_path=f"/accounts/{context.account_id}/documents/{hit.document_id}/download",
            )
            for hit in hits
            if (document := documents.get(hit.document_id)) is not None
        ]
        return SearchResponse(query=query, model=vector_name, results=results)
