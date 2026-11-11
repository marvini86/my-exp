from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from models.category import Category
from models.document import Document
from models.financial import EntryType, FinancialEntry, PaymentStatus
from repositories.financial_queries import _contains


@dataclass(frozen=True)
class EntryFilters:
    start: date | None = None
    end: date | None = None
    entry_type: EntryType | None = None
    document_type: str | None = None
    payment_status: PaymentStatus | None = None
    category_id: UUID | None = None
    uncategorized: bool = False
    document_id: UUID | None = None
    # Free text: description or establishment (accent/case insensitive).
    q: str | None = None


@dataclass(frozen=True)
class EntryListRow:
    entry: FinancialEntry
    original_filename: str | None
    document_type: str | None
    category_name: str | None


class FinancialEntryRepository:
    """Account entries. Filters by account_id on top of RLS."""

    def __init__(self, session: Session):
        self.session = session

    def _base(self):
        return (
            select(FinancialEntry, Document.original_filename, Document.document_type, Category.name)
            .outerjoin(
                Document, (Document.id == FinancialEntry.document_id) & (Document.account_id == FinancialEntry.account_id)
            )
            .outerjoin(
                Category,
                (Category.id == FinancialEntry.category_id) & (Category.account_id == FinancialEntry.account_id),
            )
        )

    @staticmethod
    def _conditions(account_id: UUID, f: EntryFilters) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = [FinancialEntry.account_id == account_id]
        if f.start:
            conditions.append(FinancialEntry.occurred_at >= f.start)
        if f.end:
            conditions.append(FinancialEntry.occurred_at <= f.end)
        if f.entry_type:
            conditions.append(FinancialEntry.entry_type == f.entry_type)
        if f.document_type:
            conditions.append(Document.document_type == f.document_type)
        if f.payment_status:
            conditions.append(FinancialEntry.payment_status == f.payment_status)
        if f.document_id:
            conditions.append(FinancialEntry.document_id == f.document_id)
        if f.uncategorized:
            conditions.append(FinancialEntry.category_id.is_(None))
        elif f.category_id:
            conditions.append(FinancialEntry.category_id == f.category_id)
        if f.q and f.q.strip():
            conditions.append(
                _contains(FinancialEntry.description, f.q) | _contains(func.coalesce(FinancialEntry.establishment, ""), f.q)
            )
        return conditions

    def list_page(self, account_id: UUID, filters: EntryFilters, limit: int, offset: int) -> tuple[list[EntryListRow], int]:
        conditions = self._conditions(account_id, filters)
        total = self.session.scalar(
            select(func.count())
            .select_from(FinancialEntry)
            .outerjoin(
                Document, (Document.id == FinancialEntry.document_id) & (Document.account_id == FinancialEntry.account_id)
            )
            .where(*conditions)
        )
        stmt = (
            self._base()
            .where(*conditions)
            .order_by(FinancialEntry.occurred_at.desc(), FinancialEntry.created_at.desc(), FinancialEntry.id)
            .limit(limit)
            .offset(offset)
        )
        rows = [EntryListRow(*row) for row in self.session.execute(stmt).all()]
        return rows, total or 0

    def expense_total(self, account_id: UUID, filters: EntryFilters) -> Decimal:
        """Sum of the expenses matching the filters (income/refunds excluded)."""
        total = self.session.scalar(
            select(func.coalesce(func.sum(FinancialEntry.amount), 0))
            .select_from(FinancialEntry)
            .outerjoin(
                Document, (Document.id == FinancialEntry.document_id) & (Document.account_id == FinancialEntry.account_id)
            )
            .where(
                *self._conditions(account_id, filters),
                FinancialEntry.entry_type == EntryType.EXPENSE,
                FinancialEntry.needs_review.is_(False),
            )
        )
        return Decimal(total)

    def get_row(self, account_id: UUID, entry_id: UUID) -> EntryListRow | None:
        row = self.session.execute(
            self._base().where(FinancialEntry.account_id == account_id, FinancialEntry.id == entry_id)
        ).one_or_none()
        return EntryListRow(*row) if row else None

    def get_for_update(self, account_id: UUID, entry_id: UUID) -> FinancialEntry | None:
        stmt = select(FinancialEntry).where(FinancialEntry.account_id == account_id, FinancialEntry.id == entry_id)
        return self.session.scalars(stmt).one_or_none()

    def open_bills(self, account_id: UUID, due_from: date, due_until: date, limit: int = 20) -> list[EntryListRow]:
        """Due within the interval and not marked as paid/cancelled."""
        stmt = (
            self._base()
            .where(
                FinancialEntry.account_id == account_id,
                FinancialEntry.due_date.between(due_from, due_until),
                FinancialEntry.payment_status.not_in([PaymentStatus.PAID, PaymentStatus.CANCELLED]),
                FinancialEntry.needs_review.is_(False),
            )
            .order_by(FinancialEntry.due_date, FinancialEntry.id)
            .limit(limit)
        )
        return [EntryListRow(*row) for row in self.session.execute(stmt).all()]
