
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.config import settings
from core.exceptions import AppError, ServiceUnavailableError, TooManyRequestsError, UnauthorizedError
from database.connection import engine
from schemas.common import ErrorResponse, HealthResponse

from routes.admin import router as admin_router
from routes.auth import router as auth_router
from routes.user import router as user_router
from routes.account import router as account_router
from routes.account_member import router as account_member_router
from routes.category import router as category_router
from routes.document import router as document_router
from routes.financial import router as financial_router
from routes.conversation import router as conversation_router
from routes.search import router as search_router

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# HTTP clients at DEBUG log full headers (cookies, tokens): never below WARNING.
for _noisy in ("httpx", "httpx2", "httpcore", "openrouter"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting application (environment=%s)...", settings.environment)

    if settings.environment == "development":
        logger.warning(
            "DEV auth enabled: users are identified by the X-Dev-User-Id header. "
            "Never use ENVIRONMENT=development in production."
        )
    logger.info("Checking database connection...")

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

        logger.info("Database connection established successfully.")

    except SQLAlchemyError:
        logger.exception("Failed to connect to the database.")
        raise

    yield

    logger.info("Shutting down application...")
    logger.info("Disposing database connection pool...")

    engine.dispose()

    logger.info("Database connection pool disposed successfully.")


API_DESCRIPTION = """
Personal and family expense assistant.

**Authentication:** `POST /auth/google` with the Google ID token returns a
short-lived access token (Bearer) and a refresh token (`POST /auth/refresh`).
With `ENVIRONMENT=development` the `X-Dev-User-Id` header is also accepted.

**Accounts:** all data belongs to an account (`/accounts/{account_id}/...`);
non-members get 404.

**Errors:** `{"detail": "message"}`; validation (422) carries the error list.
429 means rate limited (see `Retry-After`).
"""

TAGS = [
    {"name": "Auth", "description": "Google login, token refresh and logout."},
    {"name": "Users", "description": "Current user and members of the same accounts."},
    {"name": "Accounts", "description": "Accounts (tenants): family, personal..."},
    {"name": "Account members", "description": "Members, roles and e-mail invitations."},
    {"name": "Categories", "description": "The account's financial categories."},
    {"name": "Documents", "description": "Upload, processing status and download."},
    {"name": "Entries", "description": "Entries extracted from documents; categorization."},
    {"name": "Reports", "description": "Dashboard: totals per month, category, store and bills due."},
    {"name": "Products", "description": "Price history of purchased products."},
    {"name": "Search", "description": "Semantic search over the account's documents."},
    {"name": "Assistant", "description": "Conversations: questions about spending, answered with sources."},
    {"name": "Admin", "description": "Platform administration: who can sign in (AUTH_ADMIN_EMAILS)."},
    {"name": "Health", "description": "Availability check."},
]

app = FastAPI(
    title="My Exp API",
    version="0.8.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS,
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_visible else None,
    redoc_url="/redoc" if settings.docs_visible else None,
    openapi_url="/openapi.json" if settings.docs_visible else None,
)

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not authenticated"},
    403: {"model": ErrorResponse, "description": "Forbidden (insufficient role)"},
    404: {"model": ErrorResponse, "description": "Not found (or an account you are not a member of)"},
}

app.include_router(auth_router, responses={401: _ERRORS[401], 429: {"model": ErrorResponse, "description": "Muitas tentativas"}})
for _router in (
    admin_router,
    user_router,
    account_router,
    account_member_router,
    category_router,
    document_router,
    financial_router,
    search_router,
    conversation_router,
):
    app.include_router(_router, responses=_ERRORS)


# API JSON bodies are small (largest: a 2000-character question).
# Uploads (multipart) have their own limit on the documents route.
MAX_JSON_BODY_BYTES = 64 * 1024


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_type = request.headers.get("content-type", "")
    declared = request.headers.get("content-length")
    if not content_type.startswith("multipart/form-data") and declared and declared.isdigit():
        if int(declared) > MAX_JSON_BODY_BYTES:
            return JSONResponse(status_code=413, content={"detail": "Request body too large"})
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # Responses carry financial data and tokens: no intermediate caching.
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


def add_cors(target: FastAPI, origins: list[str]) -> None:
    """With no configured origins, no browser from another domain is accepted."""
    if not origins:
        return
    target.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Bearer in the header, no cookies: credentials not needed.
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"]
        + (["X-Dev-User-Id"] if settings.environment == "development" else []),
        expose_headers=["Retry-After"],
        max_age=600,
    )


# Added after the middleware above = outermost: answers preflight (OPTIONS) directly.
add_cors(app, settings.cors_origins)


@app.get("/health", response_model=HealthResponse, tags=["Health"], responses={503: {"model": HealthResponse}})
def health() -> JSONResponse:
    """Liveness + database. Does not call external services (AI, Qdrant, Azure)."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("Health check: database unavailable")
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unavailable"})
    return JSONResponse(content={"status": "ok", "database": "ok"})


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    # RFC 6750: 401 tells the client which scheme is expected.
    headers = None
    if isinstance(exc, UnauthorizedError):
        headers = {"WWW-Authenticate": "Bearer"}
    elif isinstance(exc, TooManyRequestsError):
        headers = {"Retry-After": str(exc.retry_after)}
    elif isinstance(exc, ServiceUnavailableError) and exc.retry_after:
        headers = {"Retry-After": str(exc.retry_after)}
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message},
        headers=headers,
    )


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    # Details stay in the log only; the client gets a generic message.
    logger.exception("Database error | path=%s", request.url.path)

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    # Logs only the location and type of the error, never the submitted values
    # (they may contain personal or financial data).
    logger.warning(
        "Validation error | path=%s | errors=%s",
        request.url.path,
        [{"loc": error["loc"], "type": error["type"]} for error in exc.errors()],
    )

    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors())},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Last resort: same error format and no stack trace for the client.
    logger.exception("Unhandled error | path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
