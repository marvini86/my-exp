"""Assistant orchestration and conversation management (private per user).

Flow of a question:
1. stores the user's message (commit: nothing stays locked during the LLM calls);
2. the LLM plans (intent + parameters) → code normalizes the plan;
3. fixed tools run (SQL under RLS / semantic search);
4. the LLM writes the answer from the facts; citations are validated;
5. stores the answer with citations and metadata (plan, tool, models).
"""

import logging
from collections.abc import Callable
from datetime import date
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.account_context import AccountContext
from core.exceptions import NotFoundError, ServiceUnavailableError
from models.conversation import Conversation, Message, MessageRole
from repositories.conversation import ConversationRepository
from schemas.conversation import AskResponse, ConversationResponse, CreateConversationRequest, MessageResponse
from services.assistant.answer import OUT_OF_SCOPE_ANSWER, build_answer_messages, resolve_citations
from services.assistant.planner import Intent, QueryPlan, build_planner_messages, normalize_plan
from services.assistant.tools import AssistantTools
from services.llm_router import LLMOutputError, LLMRequestError, LLMRouter, LLMUnavailableError

logger = logging.getLogger(__name__)

TEMPORARILY_UNAVAILABLE = "Assistant temporarily unavailable, try again in a few minutes"
DAILY_LIMIT_REACHED = "AI daily limit reached"

HISTORY_MESSAGES = 6
_TITLE_LENGTH = 80


class AssistantService:
    def __init__(
        self,
        session: Session,
        conversations: ConversationRepository,
        tools: AssistantTools,
        llm: LLMRouter,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.session = session
        self.conversations = conversations
        self.tools = tools
        self.llm = llm
        self.today = today

    # -- conversas ------------------------------------------------------------

    def create_conversation(self, context: AccountContext, request: CreateConversationRequest) -> ConversationResponse:
        conversation = self.conversations.add(
            Conversation(account_id=context.account_id, user_id=context.user.id, title=request.title)
        )
        self.session.commit()
        return ConversationResponse.model_validate(conversation)

    def list_conversations(self, context: AccountContext) -> list[ConversationResponse]:
        return [
            ConversationResponse.model_validate(c)
            for c in self.conversations.list_owned(context.account_id, context.user.id)
        ]

    def list_messages(self, context: AccountContext, conversation_id: UUID) -> list[MessageResponse]:
        self._owned(context, conversation_id)
        return [
            MessageResponse.model_validate(m)
            for m in self.conversations.list_messages(context.account_id, conversation_id)
        ]

    # -- perguntas ------------------------------------------------------------

    def ask(self, context: AccountContext, conversation_id: UUID, question: str) -> AskResponse:
        conversation = self._owned(context, conversation_id)
        history = [
            (m.role.value, m.content)
            for m in self.conversations.recent_messages(context.account_id, conversation_id, limit=HISTORY_MESSAGES)
            if m.role in (MessageRole.USER, MessageRole.ASSISTANT)
        ]

        user_message = self.conversations.add_message(
            Message(account_id=context.account_id, conversation_id=conversation_id, role=MessageRole.USER, content=question)
        )
        if not conversation.title:
            conversation.title = question[:_TITLE_LENGTH]
        conversation.updated_at = func.now()
        self.session.commit()

        today = self.today()
        plan, planner_model = self._plan(question, history, today)

        if plan.intent is Intent.OUT_OF_SCOPE:
            text, citations, answer_model, tool = OUT_OF_SCOPE_ANSWER, [], None, "none"
        else:
            output = self.tools.run(plan, context, today)
            tool = output.tool
            raw_answer, answer_model = self._llm_text(build_answer_messages(question, history, output))
            text, citations = resolve_citations(raw_answer, output.sources, context.account_id)

        assistant_message = self.conversations.add_message(
            Message(
                account_id=context.account_id,
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=text,
                citations=citations,
                metadata_={
                    "plan": plan.model_dump(mode="json"),
                    "tool": tool,
                    "planner_model": planner_model,
                    "answer_model": answer_model,
                },
            )
        )
        conversation.updated_at = func.now()
        self.session.commit()

        logger.info(
            "Assistant answered | conversation=%s intent=%s tool=%s citations=%s",
            conversation_id,
            plan.intent,
            tool,
            len(citations),
        )
        return AskResponse(
            user_message=MessageResponse.model_validate(user_message),
            assistant_message=MessageResponse.model_validate(assistant_message),
        )

    # -- internos -------------------------------------------------------------

    def _owned(self, context: AccountContext, conversation_id: UUID) -> Conversation:
        conversation = self.conversations.get_owned(context.account_id, conversation_id, context.user.id)
        if conversation is None:
            # Another member's conversation is also 404: conversations are private.
            raise NotFoundError("Conversation not found")
        return conversation

    def _plan(self, question: str, history: list[tuple[str, str]], today: date) -> tuple[QueryPlan, str | None]:
        try:
            plan, model = self.llm.structured(build_planner_messages(question, history, today), QueryPlan)
        except LLMOutputError:
            # No model managed to plan: searching the documents is a safe fallback.
            logger.warning("Planner produced no valid plan; falling back to document search")
            plan, model = QueryPlan(intent=Intent.DOCUMENT_SEARCH, search_query=question), None
        except LLMUnavailableError as exc:
            raise _unavailable(exc) from exc
        except LLMRequestError as exc:
            raise ServiceUnavailableError(TEMPORARILY_UNAVAILABLE) from exc
        return normalize_plan(plan, question, today), model

    def _llm_text(self, messages: list) -> tuple[str, str]:
        try:
            return self.llm.text(messages)
        except LLMUnavailableError as exc:
            raise _unavailable(exc) from exc
        except (LLMOutputError, LLMRequestError) as exc:
            raise ServiceUnavailableError(TEMPORARILY_UNAVAILABLE) from exc



def _unavailable(exc: LLMUnavailableError) -> ServiceUnavailableError:
    """503 with Retry-After: the client knows whether it is a matter of minutes or of the daily limit."""
    message = DAILY_LIMIT_REACHED if exc.daily_limit else TEMPORARILY_UNAVAILABLE
    return ServiceUnavailableError(message, retry_after=max(int(exc.delay_seconds), 1))
