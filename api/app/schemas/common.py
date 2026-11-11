from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Error format of the whole API (except 422, which carries Pydantic's list)."""

    detail: str


class HealthResponse(BaseModel):
    status: str
    database: str
