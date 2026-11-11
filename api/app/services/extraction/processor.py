"""Extraction processor: extracts, validates and persists entries and items.

Runs inside the worker transaction that marks the document as completed:
either everything is written (entry, items, extracted data) or nothing is.
"""

import logging
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.document import Document, IngestionJob, JobType
from models.financial import DocumentItem, EntryType, FinancialEntry
from repositories.category import CategoryRepository
from repositories.document import DocumentRepository, IngestionJobRepository
from repositories.financial import FinancialRepository
from schemas.extraction import SPENDING_CATEGORY_NAMES, ExtractedDocument, ExtractedItem
from services.document_processor import IntegrityCheckProcessor
from services.extraction.base import Extractor
from services.extraction.categorizer import categorize
from services.extraction.access_key import parse_access_key
from services.extraction.validation import ValidationResult, review_reasons, validate_extraction

logger = logging.getLogger(__name__)


class ExtractionProcessor:
    def __init__(self, extractors: Sequence[Extractor]) -> None:
        self.extractors = list(extractors)
        self.integrity = IntegrityCheckProcessor()

    def process(self, session: Session, document: Document, content: bytes) -> None:
        self.integrity.process(session, document, content)

        mime_type = document.mime_type or ""
        extractor = next((e for e in self.extractors if e.supports(mime_type)), None)
        if extractor is None:
            document.extracted_data = {"status": "skipped", "reason": f"No extractor for {document.mime_type}"}
            logger.info("Extraction skipped | document=%s mime=%s", document.id, document.mime_type)
            return

        extracted = extractor.extract(content, mime_type)
        validation = validate_extraction(extracted)

        # The same receipt in another file (another photo, or photo + XML) does not become another expense.
        original = self._claim_access_key(session, document, extracted)
        if original is not None:
            self._mark_duplicate(session, document, extracted, original, type(extractor).__name__)
            return

        category = categorize(extracted)
        category_id = CategoryRepository(session).get_or_create(document.account_id, SPENDING_CATEGORY_NAMES[category])

        repository = FinancialRepository(session)
        repository.delete_by_document(document.account_id, document.id)
        entry = self._build_entry(document, extracted, validation)
        # Same store (CNPJ) always with the same name in the account: otherwise "where did I buy
        # it cheapest" and the totals per establishment get split across spellings.
        # Preference: trade name (how the family knows the store) > name already used in the account.
        tax_id = extracted.establishment_tax_id
        has_trade_name = bool(entry.establishment) and entry.establishment != extracted.metadata.get("legal_name")
        if tax_id and has_trade_name and "legal_name" in extracted.metadata:
            repository.rename_establishment(document.account_id, tax_id, entry.establishment)
        elif tax_id:
            known = repository.establishment_name_for_tax_id(document.account_id, tax_id)
            entry.establishment = known or entry.establishment
        entry.category_id = category_id

        reasons = review_reasons(extracted)
        if not document.access_key and tax_id:
            duplicate_of = repository.possible_duplicate(
                document.account_id, tax_id, entry.occurred_at, entry.amount, document.id
            )
            if duplicate_of:
                reasons.append({"code": "possible_duplicate", "document_id": str(duplicate_of)})
        # Still doubtful after the re-reads: left out of the totals until someone confirms it.
        entry.needs_review = bool(reasons)

        repository.add_entry(
            entry,
            [self._build_item(document, item) for item in extracted.items],
        )

        document.document_type = extracted.document_type.value
        document.extracted_data = {
            "status": "extracted",
            "extractor": type(extractor).__name__,
            "warnings": validation.warnings,
            "review": {"pending": bool(reasons), "reasons": reasons},
            # Final store name (standardized by CNPJ): semantic search indexes from here.
            "result": extracted.model_dump(mode="json") | {"establishment": entry.establishment},
        }
        document.indexed_at = None

        # Indexing for semantic search is a separate job: if embeddings
        # are unavailable, only indexing is retried (never the extraction).
        IngestionJobRepository(session).add(
            IngestionJob(account_id=document.account_id, document_id=document.id, job_type=JobType.INDEX)
        )

        logger.info(
            "Extraction completed | document=%s type=%s category=%s items=%s warnings=%s review=%s",
            document.id,
            extracted.document_type,
            category,
            len(extracted.items),
            len(validation.warnings),
            [r["code"] for r in reasons],
        )

    @staticmethod
    def _claim_access_key(session: Session, document: Document, extracted: ExtractedDocument) -> Document | None:
        """Stores the access key on the document. Already in the account → returns the original."""
        key = parse_access_key(extracted.metadata.get("access_key"))
        if key is None:
            document.access_key = None
            return None
        documents = DocumentRepository(session)
        original = documents.get_by_access_key(document.account_id, key.key, document.id)
        if original is not None:
            return original
        document.access_key = key.key
        try:
            with session.begin_nested():  # the unique index is the guarantee against concurrent workers
                session.flush()
        except IntegrityError:
            document.access_key = None
            return documents.get_by_access_key(document.account_id, key.key, document.id)
        return None

    @staticmethod
    def _mark_duplicate(
        session: Session, document: Document, extracted: ExtractedDocument, original: Document, extractor: str
    ) -> None:
        """Same receipt as another document: no entry (and no indexing)."""
        FinancialRepository(session).delete_by_document(document.account_id, document.id)
        document.access_key = None
        document.document_type = extracted.document_type.value
        document.extracted_data = {
            "status": "duplicate",
            "extractor": extractor,
            "duplicate_of": str(original.id),
            "result": extracted.model_dump(mode="json"),
        }
        logger.info("Duplicate receipt | document=%s original=%s", document.id, original.id)

    @staticmethod
    def _build_entry(document: Document, extracted: ExtractedDocument, validation: ValidationResult) -> FinancialEntry:
        return FinancialEntry(
            account_id=document.account_id,
            document_id=document.id,
            entry_type=EntryType.EXPENSE,
            description=(extracted.description or document.original_filename)[:500],
            establishment=extracted.establishment,
            amount=validation.amount,
            currency=extracted.currency,
            occurred_at=extracted.issued_at or extracted.due_date or document.created_at.date(),
            due_date=extracted.due_date,
            paid_at=extracted.paid_at,
            payment_status=extracted.payment_status,
            metadata_={
                **extracted.metadata,
                "document_type": extracted.document_type.value,
                "establishment_tax_id": extracted.establishment_tax_id,
            },
        )

    @staticmethod
    def _build_item(document: Document, item: ExtractedItem) -> DocumentItem:
        return DocumentItem(
            account_id=document.account_id,
            document_id=document.id,
            description=item.description,
            brand=item.brand,
            sku=item.sku,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            total_price=item.total_price,
            metadata_=item.metadata,
        )
