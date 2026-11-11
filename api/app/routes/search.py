from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.account_context import AccountContext, get_account_context
from core.rate_limit import SEMANTIC_SEARCH, rate_limit
from database.connection import get_db
from repositories.document import DocumentRepository
from repositories.vector_store import VectorStore
from schemas.search import SearchResponse
from services.embeddings import EmbeddingRouter
from services.search import SearchService, get_embedding_router, get_vector_store

router = APIRouter(prefix="/accounts/{account_id}/search", tags=["Search"])


def get_search_service(
    session: Session = Depends(get_db),
    embeddings: EmbeddingRouter = Depends(get_embedding_router),
    store: VectorStore = Depends(get_vector_store),
) -> SearchService:
    return SearchService(DocumentRepository(session), embeddings, store)


@router.get("/", response_model=SearchResponse, dependencies=[Depends(rate_limit(SEMANTIC_SEARCH))])
def search_documents(
    q: Annotated[str, Query(min_length=2, max_length=500, description="Question or search terms")],
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    document_type: Annotated[str | None, Query(max_length=50)] = None,
    context: AccountContext = Depends(get_account_context),
    service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """Semantic search over the account's documents (most relevant chunks + source document)."""
    return service.search(context, q.strip(), limit=limit, document_type=document_type)
