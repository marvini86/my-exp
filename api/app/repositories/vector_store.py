"""Qdrant collection access (semantic search).

Multi-tenant: `account_id` is a REQUIRED parameter of every search and delete, and
the field is indexed as a tenant (`is_tenant=True`), which isolates and speeds up
filtering by account. There is no method that queries the collection without an account.
"""

import logging
import uuid
from dataclasses import dataclass
from uuid import UUID

from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from core.exceptions import ServiceUnavailableError

logger = logging.getLogger(__name__)

# Fixed namespace: the point id is deterministic (idempotent reindexing).
_POINT_NAMESPACE = uuid.UUID("6f1d3c2a-6a55-4b8e-9d7e-6b1b3f0a9c11")

_QDRANT_ERRORS = (ResponseHandlingException, UnexpectedResponse, ConnectionError, TimeoutError)


@dataclass(frozen=True)
class Chunk:
    document_id: UUID
    index: int
    content: str
    kind: str  # "summary" (extracted data) | "text" (document text)
    document_type: str | None = None


@dataclass(frozen=True)
class SearchHit:
    document_id: UUID
    chunk_index: int
    content: str
    kind: str
    document_type: str | None
    score: float


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str) -> None:
        self.client = client
        self.collection = collection
        self._vector_names: set[str] | None = None

    def ensure_collection(self, dimensions: dict[str, int]) -> None:
        """Creates the collection (vectors named after each model) and the payload indexes, if missing."""
        try:
            if not self.client.collection_exists(self.collection):
                if not dimensions:
                    raise ServiceUnavailableError("Cannot create collection: no embedding model available")
                self.client.create_collection(
                    self.collection,
                    vectors_config={
                        name: models.VectorParams(size=size, distance=models.Distance.COSINE)
                        for name, size in dimensions.items()
                    },
                )
                self.client.create_payload_index(
                    self.collection,
                    "account_id",
                    field_schema=models.KeywordIndexParams(type=models.KeywordIndexType.KEYWORD, is_tenant=True),
                )
                for field in ("document_id", "document_type"):
                    self.client.create_payload_index(self.collection, field, models.PayloadSchemaType.KEYWORD)
                logger.info("Qdrant collection created | collection=%s vectors=%s", self.collection, dimensions)
        except _QDRANT_ERRORS as exc:
            raise ServiceUnavailableError("Search index unavailable") from exc

        self._vector_names = None
        missing = set(dimensions) - self.vector_names()
        if missing:
            logger.warning(
                "Embedding models not present in collection (recreate it to add them) | missing=%s",
                sorted(missing),
            )

    def vector_names(self) -> set[str]:
        if self._vector_names is None:
            try:
                info = self.client.get_collection(self.collection)
            except _QDRANT_ERRORS as exc:
                raise ServiceUnavailableError("Search index unavailable") from exc
            vectors = info.config.params.vectors
            self._vector_names = set(vectors) if isinstance(vectors, dict) else set()
        return self._vector_names

    def replace_document(
        self,
        account_id: UUID,
        document_id: UUID,
        chunks: list[Chunk],
        vectors: dict[str, list[list[float]]],
    ) -> int:
        """Replaces every point of the document. Returns how many points were written."""
        names = [name for name in vectors if name in self.vector_names()]
        if not names:
            raise ServiceUnavailableError("No embedding model matches the collection vectors")

        points = [
            models.PointStruct(
                id=str(uuid.uuid5(_POINT_NAMESPACE, f"{document_id}:{chunk.index}")),
                vector={name: vectors[name][position] for name in names},
                payload={
                    "account_id": str(account_id),
                    "document_id": str(document_id),
                    "chunk_index": chunk.index,
                    "kind": chunk.kind,
                    "document_type": chunk.document_type,
                    "content": chunk.content,
                },
            )
            for position, chunk in enumerate(chunks)
        ]

        try:
            self.delete_document(account_id, document_id)
            if points:
                self.client.upsert(self.collection, points=points, wait=True)
        except _QDRANT_ERRORS as exc:
            raise ServiceUnavailableError("Search index unavailable") from exc
        return len(points)

    def delete_document(self, account_id: UUID, document_id: UUID) -> None:
        try:
            self.client.delete(
                self.collection,
                points_selector=models.FilterSelector(
                    filter=self._filter(account_id, models.FieldCondition(
                        key="document_id", match=models.MatchValue(value=str(document_id))
                    ))
                ),
                wait=True,
            )
        except _QDRANT_ERRORS as exc:
            raise ServiceUnavailableError("Search index unavailable") from exc

    def search(
        self,
        account_id: UUID,
        vector_name: str,
        vector: list[float],
        *,
        limit: int = 5,
        document_type: str | None = None,
    ) -> list[SearchHit]:
        if vector_name not in self.vector_names():
            raise ServiceUnavailableError("Embedding model not indexed in the collection")

        extra = (
            [models.FieldCondition(key="document_type", match=models.MatchValue(value=document_type))]
            if document_type
            else []
        )
        try:
            response = self.client.query_points(
                self.collection,
                query=vector,
                using=vector_name,
                query_filter=self._filter(account_id, *extra),
                limit=limit,
                with_payload=True,
            )
        except _QDRANT_ERRORS as exc:
            raise ServiceUnavailableError("Search index unavailable") from exc

        return [
            SearchHit(
                document_id=UUID(point.payload["document_id"]),
                chunk_index=point.payload.get("chunk_index", 0),
                content=point.payload.get("content", ""),
                kind=point.payload.get("kind", "text"),
                document_type=point.payload.get("document_type"),
                score=point.score,
            )
            for point in response.points
        ]

    @staticmethod
    def _filter(account_id: UUID, *conditions: models.FieldCondition) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(key="account_id", match=models.MatchValue(value=str(account_id))),
                *conditions,
            ]
        )
