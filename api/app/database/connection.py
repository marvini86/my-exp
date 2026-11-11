import logging

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from database.pool import ping_when_idle

# Registers the listener that reapplies the tenant context (RLS) on every transaction.
import database.tenant_context  # noqa: F401, E402

logger = logging.getLogger(__name__)

def build_database_url(username: str, password: str) -> URL:
    """URL of the database configured in DB_*, with the given credentials."""
    return URL.create(
        drivername="postgresql+psycopg",
        username=username,
        password=password,
        host=settings.db.host,
        port=settings.db.port,
        database=settings.db.name,
    )


DATABASE_URL = build_database_url(settings.db.user, settings.db.password)

# Small pool: the VPS has little RAM and every connection costs memory
# both in the application and in PostgreSQL.
engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=5,
    pool_recycle=1800,
)
# Instead of pool_pre_ping (one extra round trip on every request).
ping_when_idle(engine)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)

def get_db():
    logger.debug("Opening database session...")

    try:
        with SessionLocal() as session:
            yield session
    finally:
        logger.debug("Database session closed.")