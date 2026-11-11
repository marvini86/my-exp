from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints

CategoryName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
CategoryDescription = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class CategoryResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreateCategoryRequest(BaseModel):
    name: CategoryName
    description: CategoryDescription | None = None
