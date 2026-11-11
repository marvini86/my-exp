from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

from models.conversation import MessageRole

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=2000)]


class CreateConversationRequest(BaseModel):
    title: Title | None = None


class ConversationResponse(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Citation(BaseModel):
    ref: str
    document_id: UUID
    original_filename: str
    download_path: str


class MessageResponse(BaseModel):
    id: UUID
    role: MessageRole
    content: str
    citations: list[Citation]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AskRequest(BaseModel):
    content: Question


class AskResponse(BaseModel):
    user_message: MessageResponse
    assistant_message: MessageResponse
