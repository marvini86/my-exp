from functools import lru_cache
from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.account_context import AccountContext, get_account_context
from core.config import settings
from core.rate_limit import ASSISTANT_MESSAGE, rate_limit
from database.connection import get_db
from repositories.conversation import ConversationRepository
from repositories.document import DocumentRepository
from repositories.financial_queries import FinancialQueryRepository
from repositories.vector_store import VectorStore
from schemas.conversation import (
    AskRequest,
    AskResponse,
    ConversationResponse,
    CreateConversationRequest,
    MessageResponse,
)
from services.assistant.service import AssistantService
from services.assistant.tools import AssistantTools
from services.embeddings import EmbeddingRouter
from services.llm_router import LLMRouter
from services.search import SearchService, get_embedding_router, get_vector_store

router = APIRouter(prefix="/accounts/{account_id}/conversations", tags=["Assistant"])


@lru_cache
def get_llm_router() -> LLMRouter:
    """One router per process (keeps the model catalog and cooldowns)."""
    # Text models from .env (LLM_TEXT_MODELS); without them, OpenRouter.
    return LLMRouter.for_purpose(settings, vision=False)


def get_assistant_service(
    session: Session = Depends(get_db),
    llm: LLMRouter = Depends(get_llm_router),
    embeddings: EmbeddingRouter = Depends(get_embedding_router),
    store: VectorStore = Depends(get_vector_store),
) -> AssistantService:
    tools = AssistantTools(
        FinancialQueryRepository(session),
        SearchService(DocumentRepository(session), embeddings, store),
    )
    return AssistantService(session, ConversationRepository(session), tools, llm)


@router.post("/", response_model=ConversationResponse, status_code=HTTPStatus.CREATED)
def create_conversation(
    request: CreateConversationRequest,
    context: AccountContext = Depends(get_account_context),
    service: AssistantService = Depends(get_assistant_service),
) -> ConversationResponse:
    return service.create_conversation(context, request)


@router.get("/", response_model=list[ConversationResponse])
def list_conversations(
    context: AccountContext = Depends(get_account_context),
    service: AssistantService = Depends(get_assistant_service),
) -> list[ConversationResponse]:
    """Conversations of the current user in this account (private)."""
    return service.list_conversations(context)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conversation_id: UUID,
    context: AccountContext = Depends(get_account_context),
    service: AssistantService = Depends(get_assistant_service),
) -> list[MessageResponse]:
    return service.list_messages(context, conversation_id)


@router.post(
    "/{conversation_id}/messages",
    response_model=AskResponse,
    status_code=HTTPStatus.CREATED,
    dependencies=[Depends(rate_limit(ASSISTANT_MESSAGE))],
)
def ask(
    conversation_id: UUID,
    request: AskRequest,
    context: AccountContext = Depends(get_account_context),
    service: AssistantService = Depends(get_assistant_service),
) -> AskResponse:
    """Asks the assistant. Synchronous: with free models it may take 15 to 40 s."""
    return service.ask(context, conversation_id, request.content)
