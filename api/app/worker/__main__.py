"""Document ingestion worker.

Usage (from app/):  python -m worker

Lightweight process, separate from the API: polls the ingestion_jobs table, processes one
job at a time and sleeps `WORKER_POLL_INTERVAL_SECONDS` when the queue is empty.
SIGINT/SIGTERM stop after the job in progress.
"""

import logging
import os
import signal
import socket
import sys
import threading
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from database.connection import build_database_url
from services.blob_storage import AzureBlobStorage
from services.extraction.llm import LLMDocumentExtractor
from services.extraction.nfe_xml import NFeXmlExtractor
from services.extraction.processor import ExtractionProcessor
from services.indexing import IndexingProcessor
from services.ingestion import IngestionService
from services.search import get_embedding_router, get_vector_store

logger = logging.getLogger("worker")


def main() -> int:
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # HTTP clients at DEBUG log full headers (cookies, tokens): never below WARNING.
    for noisy in ("httpx", "httpx2", "httpcore", "openrouter"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    config = settings.worker

    if not config.db_user or config.db_password is None:
        logger.error("WORKER_DB_USER and WORKER_DB_PASSWORD must be set (role myexp_worker)")
        return 2

    # One job at a time: two connections are enough (low memory usage).
    engine = create_engine(
        build_database_url(config.db_user, config.db_password.get_secret_value()),
        pool_size=2,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=1800,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    service = IngestionService(
        session_factory=session_factory,
        storage=AzureBlobStorage(settings.storage),
        processor=ExtractionProcessor(
            extractors=[
                NFeXmlExtractor(),  # XML: deterministic, free
                LLMDocumentExtractor.for_settings(settings),  # PDF/imagem
            ]
        ),
        index_processor=IndexingProcessor(
            get_embedding_router(),
            get_vector_store(),
            chunk_size=settings.embedding.chunk_size,
            chunk_overlap=settings.embedding.chunk_overlap,
        ),
        worker_id=worker_id,
        config=config,
    )

    stop = threading.Event()

    def request_stop(signum, _frame) -> None:
        logger.info("Signal %s received; finishing current job before exiting...", signum)
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    logger.info("Worker started | id=%s poll=%ss", worker_id, config.poll_interval_seconds)

    try:
        while not stop.is_set():
            try:
                processed = service.process_next()
            except Exception:
                # E.g. database unavailable. Wait and try again without bringing the process down.
                logger.exception("Worker iteration failed")
                processed = False

            if not processed:
                stop.wait(config.poll_interval_seconds)
    finally:
        engine.dispose()
        logger.info("Worker stopped | id=%s", worker_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
