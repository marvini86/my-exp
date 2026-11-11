"""Ingestion worker against a real PostgreSQL, with real commits.

Requires, besides TEST_DATABASE_URL (role myexp_app):
  TEST_ADMIN_DATABASE_URL  → migration role (prepares data, bypasses RLS)
  TEST_WORKER_DATABASE_URL → role myexp_worker

WARNING: these tests TRUNCATE the tables. Use a disposable database only.
"""

import hashlib
import os
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from core.config import WorkerSettings
from core.exceptions import NotFoundError, ServiceUnavailableError
from database.tenant_context import set_worker_context
from models import (
    Account,
    AccountMember,
    AccountRole,
    Document,
    DocumentItem,
    EntryType,
    FinancialEntry,
    IngestionJob,
    PaymentStatus,
    ProcessingStatus,
    User,
)
from schemas.extraction import DocumentType, ExtractedDocument, ExtractedItem
from services.document_processor import DeferProcessingError, IntegrityCheckProcessor, PermanentProcessingError
from services.extraction.nfe_xml import NFeXmlExtractor
from services.extraction.processor import ExtractionProcessor
from services.ingestion import IngestionService

ADMIN_URL = os.getenv("TEST_ADMIN_DATABASE_URL")
WORKER_URL = os.getenv("TEST_WORKER_DATABASE_URL")
APP_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (ADMIN_URL and WORKER_URL), reason="TEST_ADMIN/WORKER_DATABASE_URL não definidas"),
]

CONTENT = b"%PDF-1.7\nworker test\n%%EOF\n"
CONFIG = WorkerSettings(lock_timeout_seconds=900, retry_base_seconds=60, retry_factor=5)


class FakeStorage:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.error: Exception | None = None
        self.downloads: Counter[str] = Counter()
        self._lock = threading.Lock()

    def download(self, blob_name: str) -> bytes:
        with self._lock:
            self.downloads[blob_name] += 1
        if self.error is not None:
            raise self.error
        if blob_name not in self.blobs:
            raise NotFoundError("Blob not found")
        return self.blobs[blob_name]


@pytest.fixture(scope="module")
def admin_engine():
    engine = create_engine(ADMIN_URL)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def worker_sessions():
    engine = create_engine(WORKER_URL, pool_size=4)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(admin_engine):
    yield
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, accounts, account_members, documents, ingestion_jobs, "
                "financial_entries, document_items CASCADE"
            )
        )


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def account(admin_engine):
    with Session(admin_engine, expire_on_commit=False) as s:
        user = User(name="Owner", email=f"{uuid4().hex[:8]}@worker.test")
        account = Account(name="Worker", slug=f"worker-{uuid4().hex[:8]}")
        s.add_all([user, account])
        s.flush()
        s.add(AccountMember(account_id=account.id, user_id=user.id, role=AccountRole.OWNER))
        s.commit()
        return account


@pytest.fixture
def make_job(admin_engine, account, storage):
    """Creates document + job (as the upload would) and puts the file in storage."""

    def _make(
        content: bytes = CONTENT,
        stored: bytes | None = None,
        mime_type: str = "application/pdf",
        **job_fields,
    ) -> tuple:
        with Session(admin_engine, expire_on_commit=False) as s:
            document = Document(
                account_id=account.id,
                original_filename="nota.pdf",
                mime_type=mime_type,
                file_size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                blob_path=f"test/{uuid4()}.pdf",
            )
            s.add(document)
            s.flush()
            job = IngestionJob(account_id=account.id, document_id=document.id, **job_fields)
            s.add(job)
            s.commit()

        storage.blobs[document.blob_path] = content if stored is None else stored
        return document, job

    return _make


@pytest.fixture
def make_service(worker_sessions, storage):
    def _make(worker_id: str = "worker-test", clock=None, processor=None, index_processor=None) -> IngestionService:
        return IngestionService(
            session_factory=worker_sessions,
            storage=storage,
            processor=processor or IntegrityCheckProcessor(),
            worker_id=worker_id,
            config=CONFIG,
            index_processor=index_processor,
            **({"clock": clock} if clock else {}),
        )

    return _make


def drain(service: IngestionService) -> None:
    """Processes the queue until empty (extraction enqueues indexing right after)."""
    for _ in range(20):
        if not service.process_next():
            return


def reload(admin_engine, document, job) -> tuple[Document, IngestionJob]:
    with Session(admin_engine) as s:
        return s.get(Document, document.id), s.get(IngestionJob, job.id)


class TestProcessing:
    def test_processes_pending_job(self, admin_engine, make_job, make_service):
        document, job = make_job()
        service = make_service()

        assert service.process_next() is True
        assert service.process_next() is False  # fila vazia

        document, job = reload(admin_engine, document, job)
        assert document.processing_status is ProcessingStatus.COMPLETED
        assert (job.status, job.attempts, job.locked_by, job.last_error) == (ProcessingStatus.COMPLETED, 1, None, None)

    def test_checksum_mismatch_fails_without_retry(self, admin_engine, make_job, make_service):
        document, job = make_job(stored=b"%PDF-1.7\ntampered\n")

        make_service().process_next()

        document, job = reload(admin_engine, document, job)
        assert (job.status, job.attempts) == (ProcessingStatus.FAILED, 1)
        assert document.processing_status is ProcessingStatus.FAILED
        assert "checksum" in document.processing_error

    def test_missing_blob_fails_without_retry(self, admin_engine, make_job, make_service, storage):
        document, job = make_job()
        storage.blobs.clear()

        make_service().process_next()

        document, job = reload(admin_engine, document, job)
        assert job.status is ProcessingStatus.FAILED
        assert job.last_error.startswith("NotFoundError")

    def test_future_job_is_not_claimed(self, make_job, make_service):
        make_job(available_at=datetime.now(UTC) + timedelta(hours=1))

        assert make_service().process_next() is False


class TestRetries:
    def test_transient_errors_retry_with_backoff_then_fail(self, admin_engine, make_job, make_service, storage):
        document, job = make_job()
        storage.error = ServiceUnavailableError("File storage unavailable")
        now = datetime.now(UTC)
        service = make_service(clock=lambda: now)

        expected_delays = [timedelta(seconds=60), timedelta(seconds=300)]
        for attempt, delay in enumerate(expected_delays, start=1):
            assert service.process_next() is True
            document, job = reload(admin_engine, document, job)
            assert (job.status, job.attempts) == (ProcessingStatus.PENDING, attempt)
            assert job.available_at == now + delay
            assert job.locked_by is None
            assert job.last_error.startswith("ServiceUnavailableError")
            assert document.processing_status is ProcessingStatus.PENDING

            assert service.process_next() is False  # not available yet
            with admin_engine.begin() as conn:  # "moves the clock forward"
                conn.execute(text("UPDATE ingestion_jobs SET available_at = NOW() WHERE id = :id"), {"id": job.id})

        assert service.process_next() is True  # 3rd and last attempt
        document, job = reload(admin_engine, document, job)
        assert (job.status, job.attempts) == (ProcessingStatus.FAILED, 3)
        assert document.processing_status is ProcessingStatus.FAILED

    def test_recovers_job_abandoned_by_crashed_worker(self, admin_engine, make_job, make_service):
        document, job = make_job(
            status=ProcessingStatus.PROCESSING,
            attempts=1,
            locked_by="crashed-worker",
            locked_at=datetime.now(UTC) - timedelta(hours=1),
        )

        assert make_service().process_next() is True

        document, job = reload(admin_engine, document, job)
        assert (job.status, job.attempts) == (ProcessingStatus.COMPLETED, 2)

    def test_recently_locked_job_is_not_stolen(self, make_job, make_service):
        make_job(
            status=ProcessingStatus.PROCESSING,
            attempts=1,
            locked_by="busy-worker",
            locked_at=datetime.now(UTC) - timedelta(minutes=1),
        )

        assert make_service().process_next() is False

    def test_abandoned_job_over_max_attempts_fails(self, admin_engine, make_job, make_service, storage):
        document, job = make_job(
            status=ProcessingStatus.PROCESSING,
            attempts=3,
            max_attempts=3,
            locked_by="crashed-worker",
            locked_at=datetime.now(UTC) - timedelta(hours=1),
        )

        make_service().process_next()

        document, job = reload(admin_engine, document, job)
        assert job.status is ProcessingStatus.FAILED
        assert "Maximum attempts" in job.last_error
        assert storage.downloads[document.blob_path] == 0


class TestDeferral:
    class AllModelsRateLimited:
        def process(self, session, document, content):
            raise DeferProcessingError("All AI models unavailable: rate_limited", 300)

    def test_deferral_does_not_consume_attempts(self, admin_engine, make_job, make_service):
        document, job = make_job()
        now = datetime.now(UTC)
        service = make_service(clock=lambda: now, processor=self.AllModelsRateLimited())

        for _ in range(5):  # more times than max_attempts
            assert service.process_next() is True
            with admin_engine.begin() as conn:
                conn.execute(text("UPDATE ingestion_jobs SET available_at = NOW() WHERE id = :id"), {"id": job.id})

        document, job = reload(admin_engine, document, job)
        assert (job.status, job.attempts) == (ProcessingStatus.PENDING, 0)
        assert job.last_error.startswith("DeferProcessingError")
        assert job.locked_by is None
        assert document.processing_status is ProcessingStatus.PENDING

    def test_deferred_job_waits_for_delay(self, admin_engine, make_job, make_service):
        document, job = make_job()
        now = datetime.now(UTC)
        service = make_service(clock=lambda: now, processor=self.AllModelsRateLimited())

        service.process_next()

        _, job = reload(admin_engine, document, job)
        assert job.available_at == now + timedelta(seconds=300)
        assert service.process_next() is False


class TestConcurrency:
    def test_concurrent_workers_never_process_the_same_job(self, admin_engine, make_job, make_service, storage):
        jobs = [make_job(content=CONTENT + str(i).encode()) for i in range(12)]
        services = [make_service(worker_id=f"worker-{n}") for n in range(3)]

        def drain(service: IngestionService) -> None:
            while service.process_next():
                pass

        threads = [threading.Thread(target=drain, args=(s,)) for s in services]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        assert all(storage.downloads[doc.blob_path] == 1 for doc, _ in jobs)
        with Session(admin_engine) as s:
            statuses = s.execute(text("SELECT status, attempts FROM ingestion_jobs")).all()
        assert set(statuses) == {("completed", 1)}


class TestExtraction:
    """Real worker (myexp_worker + RLS) extracting and writing entries and items."""

    NFCE = (Path(__file__).parent.parent / "fixtures" / "nfce_supermercado.xml").read_bytes()

    @pytest.fixture
    def extraction_service(self, make_service):
        return make_service(processor=ExtractionProcessor(extractors=[NFeXmlExtractor()]))

    @staticmethod
    def financial_rows(admin_engine, document):
        with Session(admin_engine) as s:
            entries = s.scalars(select(FinancialEntry).where(FinancialEntry.document_id == document.id)).all()
            items = s.scalars(
                select(DocumentItem).where(DocumentItem.document_id == document.id).order_by(DocumentItem.description)
            ).all()
            return entries, items

    def test_nfce_xml_creates_entry_and_items(self, admin_engine, make_job, extraction_service):
        document, job = make_job(content=self.NFCE, mime_type="application/xml")

        assert extraction_service.process_next() is True

        document, job = reload(admin_engine, document, job)
        assert job.status is ProcessingStatus.COMPLETED
        assert document.document_type == "nfce"
        assert document.extracted_data["status"] == "extracted"
        assert document.extracted_data["warnings"] == []

        entries, items = self.financial_rows(admin_engine, document)
        assert len(entries) == 1
        entry = entries[0]
        assert (entry.amount, entry.establishment, entry.occurred_at.isoformat()) == (
            Decimal("63.43"),
            "Mercado Exemplo",
            "2024-01-10",
        )
        assert (entry.entry_type, entry.payment_status) == (EntryType.EXPENSE, PaymentStatus.PAID)
        assert entry.metadata_["access_key"].startswith("3524")
        assert [(i.description, i.total_price) for i in items] == [
            ("ARROZ TIPO 1 5KG", Decimal("25.90")),
            ("LEITE INTEGRAL 1L", Decimal("29.34")),
            ("PAO FRANCES KG", Decimal("8.19")),
        ]
        assert {i.financial_entry_id for i in items} == {entry.id}
        assert {i.account_id for i in items} == {document.account_id}

    def test_reprocessing_is_idempotent(self, admin_engine, make_job, extraction_service):
        document, job = make_job(content=self.NFCE, mime_type="application/xml")
        extraction_service.process_next()

        with admin_engine.begin() as conn:  # re-enqueues the same document
            conn.execute(
                text("UPDATE ingestion_jobs SET status = 'pending', available_at = NOW() WHERE id = :id"),
                {"id": job.id},
            )
        extraction_service.process_next()

        entries, items = self.financial_rows(admin_engine, document)
        assert (len(entries), len(items)) == (1, 3)

    def test_same_receipt_in_another_file_is_a_duplicate(self, admin_engine, make_job, extraction_service):
        first, _ = make_job(content=self.NFCE, mime_type="application/xml")
        drain(extraction_service)
        # Same NFC-e (same access key) with different bytes: SHA-256 does not catch it.
        second, job = make_job(content=self.NFCE + b" ", mime_type="application/xml")

        drain(extraction_service)

        first, _ = reload(admin_engine, first, job)
        second, job = reload(admin_engine, second, job)
        assert job.status is ProcessingStatus.COMPLETED
        assert first.access_key is not None and second.access_key is None
        assert second.extracted_data["status"] == "duplicate"
        assert second.duplicate_of == first.id
        assert self.financial_rows(admin_engine, second) == ([], [])
        assert len(self.financial_rows(admin_engine, first)[0]) == 1
        with Session(admin_engine) as s:
            index_jobs = s.scalar(
                select(func.count()).select_from(IngestionJob).where(
                    IngestionJob.document_id == second.id, IngestionJob.job_type == "index"
                )
            )
        assert index_jobs == 0

    def test_reprocessing_keeps_the_original_access_key(self, admin_engine, make_job, extraction_service):
        document, job = make_job(content=self.NFCE, mime_type="application/xml")
        drain(extraction_service)
        with admin_engine.begin() as conn:
            conn.execute(
                text("UPDATE ingestion_jobs SET status = 'pending', available_at = NOW() WHERE id = :id"),
                {"id": job.id},
            )

        drain(extraction_service)

        document, _ = reload(admin_engine, document, job)
        assert document.extracted_data["status"] == "extracted"
        assert document.access_key is not None

    def test_pdf_without_extractor_is_completed_and_skipped(self, admin_engine, make_job, extraction_service):
        document, job = make_job()

        extraction_service.process_next()

        document, job = reload(admin_engine, document, job)
        assert job.status is ProcessingStatus.COMPLETED
        assert document.extracted_data["status"] == "skipped"
        assert self.financial_rows(admin_engine, document) == ([], [])

    def test_xml_that_is_not_nfe_fails_without_retry(self, admin_engine, make_job, extraction_service):
        document, job = make_job(content=b"<?xml version='1.0'?><invoice/>", mime_type="application/xml")

        extraction_service.process_next()

        document, job = reload(admin_engine, document, job)
        assert (job.status, job.attempts) == (ProcessingStatus.FAILED, 1)
        assert "not an NF-e" in document.processing_error
        assert self.financial_rows(admin_engine, document) == ([], [])


class FixedExtractor:
    def __init__(self, extracted):
        self.extracted = extracted

    def supports(self, mime_type):
        return True

    def extract(self, content, mime_type):
        return self.extracted


def receipt(total: str, items: list[str], **metadata) -> ExtractedDocument:
    return ExtractedDocument(
        document_type=DocumentType.NFCE,
        establishment="Mercado Teste",
        establishment_tax_id="11222333000181",
        issued_at=datetime(2026, 11, 8).date(),
        total_amount=Decimal(total),
        items=[ExtractedItem(description=f"ITEM {i}", quantity=Decimal("1"), total_price=Decimal(v)) for i, v in enumerate(items)],
        metadata=metadata,
    )


class TestReview:
    """Reading still doubtful: stored, but out of the totals until someone confirms it."""

    @staticmethod
    def run(make_job, make_service, extracted, content=CONTENT):
        document, job = make_job(content=content)
        drain(make_service(processor=ExtractionProcessor(extractors=[FixedExtractor(extracted)])))
        return document, job

    def test_items_not_adding_up_are_pending_review(self, admin_engine, make_job, make_service):
        document, job = self.run(make_job, make_service, receipt("112.15", ["50.00", "40.00"]))

        document, _ = reload(admin_engine, document, job)
        assert document.needs_review is True
        assert [r["code"] for r in document.review_reasons] == ["items_total_mismatch"]
        assert document.review_reasons[0]["items_total"] == "90.00"
        with Session(admin_engine) as s:
            entry = s.scalars(select(FinancialEntry).where(FinancialEntry.document_id == document.id)).one()
        assert entry.needs_review is True

    def test_consistent_reading_is_not_pending(self, admin_engine, make_job, make_service):
        document, job = self.run(make_job, make_service, receipt("90.00", ["50.00", "40.00"]))

        document, _ = reload(admin_engine, document, job)
        assert document.needs_review is False

    def test_same_store_date_and_total_without_key_is_a_possible_duplicate(self, admin_engine, make_job, make_service):
        first, _ = self.run(make_job, make_service, receipt("90.00", ["50.00", "40.00"]))
        second, job = self.run(make_job, make_service, receipt("90.00", ["50.00", "40.00"]), content=CONTENT + b"2")

        second, _ = reload(admin_engine, second, job)
        assert second.needs_review is True
        assert second.review_reasons == [{"code": "possible_duplicate", "document_id": str(first.id)}]


class TestIndexing:
    """Extraction enqueues indexing; the `index` job uses its own processor and does not touch the status."""

    NFCE = (Path(__file__).parent.parent / "fixtures" / "nfce_supermercado.xml").read_bytes()

    class FakeIndexer:
        def __init__(self, error: Exception | None = None) -> None:
            self.error = error
            self.indexed: list = []

        def process(self, session, document, content):
            if self.error:
                raise self.error
            self.indexed.append(document.id)
            document.indexed_at = datetime.now(UTC)

    @staticmethod
    def jobs(admin_engine, document):
        with Session(admin_engine) as s:
            return s.execute(
                text("SELECT job_type, status, attempts FROM ingestion_jobs WHERE document_id = :d ORDER BY created_at"),
                {"d": document.id},
            ).all()

    def service(self, make_service, indexer):
        return make_service(
            processor=ExtractionProcessor(extractors=[NFeXmlExtractor()]),
            index_processor=indexer,
        )

    def test_extraction_enqueues_and_runs_index_job(self, admin_engine, make_job, make_service):
        indexer = self.FakeIndexer()
        service = self.service(make_service, indexer)
        document, _ = make_job(content=self.NFCE, mime_type="application/xml")

        assert service.process_next() is True  # ingest
        assert self.jobs(admin_engine, document) == [("ingest", "completed", 1), ("index", "pending", 0)]

        assert service.process_next() is True  # index
        assert service.process_next() is False

        assert indexer.indexed == [document.id]
        with Session(admin_engine) as s:
            stored = s.get(Document, document.id)
        assert stored.indexed_at is not None
        assert stored.processing_status is ProcessingStatus.COMPLETED
        assert self.jobs(admin_engine, document)[-1] == ("index", "completed", 1)

    def test_unavailable_embeddings_defer_only_the_index_job(self, admin_engine, make_job, make_service):
        indexer = self.FakeIndexer(DeferProcessingError("All embedding models unavailable", 120))
        service = self.service(make_service, indexer)
        document, _ = make_job(content=self.NFCE, mime_type="application/xml")

        service.process_next()
        service.process_next()

        with Session(admin_engine) as s:
            stored = s.get(Document, document.id)
        assert stored.processing_status is ProcessingStatus.COMPLETED  # extraction preserved
        assert stored.indexed_at is None
        assert self.jobs(admin_engine, document)[-1] == ("index", "pending", 0)

    def test_index_failure_never_fails_the_document(self, admin_engine, make_job, make_service):
        indexer = self.FakeIndexer(PermanentProcessingError("broken"))
        service = self.service(make_service, indexer)
        document, _ = make_job(content=self.NFCE, mime_type="application/xml")

        service.process_next()
        service.process_next()

        with Session(admin_engine) as s:
            stored = s.get(Document, document.id)
        assert stored.processing_status is ProcessingStatus.COMPLETED
        assert stored.processing_error is None
        assert self.jobs(admin_engine, document)[-1] == ("index", "failed", 1)

    def test_skipped_extraction_does_not_enqueue_indexing(self, admin_engine, make_job, make_service):
        service = self.service(make_service, self.FakeIndexer())
        document, _ = make_job()  # PDF without an extractor configured in this test

        service.process_next()

        assert [row[0] for row in self.jobs(admin_engine, document)] == ["ingest"]


class TestRoleIsolation:
    def test_worker_sees_nothing_without_job_context(self, worker_sessions, make_job):
        make_job()

        with worker_sessions() as s:
            assert s.execute(text("SELECT count(*) FROM documents")).scalar_one() == 0

    def test_worker_context_requires_owned_job(self, worker_sessions, make_job):
        _, job = make_job()

        with worker_sessions() as s, pytest.raises(DBAPIError, match="not being processed"):
            set_worker_context(s, job.id, "intruder")
            # The context is applied at the start of the transaction (first query).
            s.execute(text("SELECT count(*) FROM documents"))

    def test_worker_cannot_use_member_context(self, worker_sessions, account):
        with worker_sessions() as s, pytest.raises(ProgrammingError, match="permission denied"):
            s.execute(text("SELECT app_set_account_context(:a, :a)"), {"a": account.id})

    @pytest.mark.skipif(not APP_URL, reason="TEST_DATABASE_URL não definida")
    def test_app_role_cannot_claim_jobs(self, make_job):
        make_job()
        engine = create_engine(APP_URL)
        try:
            with engine.connect() as conn, pytest.raises(ProgrammingError, match="permission denied"):
                conn.execute(text("SELECT * FROM app_claim_ingestion_job('api', interval '1 minute')"))
        finally:
            engine.dispose()
