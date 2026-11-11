"""Assistant financial queries (fixed, parameterized SQL).

The LLM never writes SQL: it only picks the tool and the parameters, which
become filters of these queries. All of them filter by `account_id` explicitly
and still run under the account's RLS.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, Date, cast, func, or_, select
from sqlalchemy.orm import Session

from models.category import Category
from models.document import Document
from models.financial import DocumentItem, EntryType, FinancialEntry
from schemas.extraction import SPENDING_CATEGORY_NAMES, SpendingCategory


def _like_pattern(term: str) -> str:
    escaped = term.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# Types the user asks for but the extractor rarely uses: a pharmacy receipt comes as
# NFC-e (access key). The category assigned during extraction completes the filter.
_TYPE_CATEGORIES = {"pharmacy_receipt": SPENDING_CATEGORY_NAMES[SpendingCategory.HEALTH]}


def _type_filter(account_id: UUID, document_types: list[str]) -> ColumnElement[bool]:
    condition = Document.document_type.in_(document_types)
    categories = [_TYPE_CATEGORIES[t] for t in document_types if t in _TYPE_CATEGORIES]
    if not categories:
        return condition
    in_category = FinancialEntry.category_id.in_(
        select(Category.id).where(Category.account_id == account_id, Category.name.in_(categories))
    )
    return or_(condition, in_category)


def _contains(column: ColumnElement, term: str) -> ColumnElement[bool]:
    """Contains `term`, ignoring accents and case."""
    return func.unaccent(column).ilike(func.unaccent(_like_pattern(term)), escape="\\")


# Receipts abbreviate: "REFRIG COCA", "DET LIQ". A long term also matches by prefix.
_PRODUCT_PREFIX = 5
_PRODUCT_PREFIX_MIN_LENGTH = 7


def _matches_product(column: ColumnElement, product: str) -> ColumnElement[bool]:
    """The item matches the product: it contains the term or, if the term is long, a word starting
    with its first 5 characters ("refrigerante" → "REFRIG ...")."""
    term = product.strip()
    condition = _contains(column, term)
    if len(term) >= _PRODUCT_PREFIX_MIN_LENGTH:
        prefix = _like_pattern(term[:_PRODUCT_PREFIX])[1:]  # without the leading "%": start of a word
        unaccented = func.unaccent(column)
        condition = (
            condition
            | unaccented.ilike(func.unaccent(prefix), escape="\\")
            | unaccented.ilike(func.unaccent("% " + prefix), escape="\\")
        )
    return condition


@dataclass(frozen=True)
class EntryRow:
    document_id: UUID | None
    original_filename: str | None
    document_type: str | None
    description: str
    establishment: str | None
    amount: Decimal
    occurred_at: date
    due_date: date | None
    payment_status: str


@dataclass(frozen=True)
class EstablishmentTotal:
    establishment: str
    total: Decimal
    count: int


@dataclass(frozen=True)
class SpendingSummary:
    total: Decimal
    count: int
    by_establishment: list[EstablishmentTotal]
    largest: list[EntryRow]


@dataclass(frozen=True)
class ItemRow:
    document_id: UUID
    original_filename: str
    description: str
    establishment: str | None
    occurred_at: date
    quantity: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    total_price: Decimal | None


@dataclass(frozen=True)
class DocumentRow:
    document_id: UUID
    original_filename: str
    document_type: str | None
    processing_status: str
    created_at: datetime
    occurred_at: date | None
    amount: Decimal | None
    establishment: str | None


@dataclass(frozen=True)
class ProductSpending:
    description: str
    purchases: int
    total_spent: Decimal
    documents: list[tuple[UUID, str]]  # (id, file name)


@dataclass(frozen=True)
class ProductTotals:
    purchases: int
    total_spent: Decimal
    # Quantity summed per unit (adding KG to UN makes no sense).
    quantity_by_unit: dict[str, Decimal]


class FinancialQueryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- gastos -----------------------------------------------------------------

    def _expense_filters(
        self,
        account_id: UUID,
        start: date | None,
        end: date | None,
        document_types: list[str] | None,
        establishment: str | None,
    ) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = [
            FinancialEntry.account_id == account_id,
            FinancialEntry.entry_type == EntryType.EXPENSE,
            # A doubtful reading only counts after it is confirmed.
            FinancialEntry.needs_review.is_(False),
        ]
        if start:
            filters.append(FinancialEntry.occurred_at >= start)
        if end:
            filters.append(FinancialEntry.occurred_at <= end)
        if document_types:
            filters.append(_type_filter(account_id, document_types))
        if establishment:
            filters.append(_contains(FinancialEntry.establishment, establishment))
        return filters

    def spending_summary(
        self,
        account_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        document_types: list[str] | None = None,
        establishment: str | None = None,
        top: int = 5,
    ) -> SpendingSummary:
        filters = self._expense_filters(account_id, start, end, document_types, establishment)
        base = select(FinancialEntry).outerjoin(
            Document, (Document.id == FinancialEntry.document_id) & (Document.account_id == FinancialEntry.account_id)
        )

        total, count = self.session.execute(
            base.with_only_columns(func.coalesce(func.sum(FinancialEntry.amount), 0), func.count()).where(*filters)
        ).one()

        by_establishment = [
            EstablishmentTotal(establishment=name or "(não informado)", total=amount, count=n)
            for name, amount, n in self.session.execute(
                base.with_only_columns(
                    FinancialEntry.establishment, func.sum(FinancialEntry.amount), func.count()
                )
                .where(*filters)
                .group_by(FinancialEntry.establishment)
                .order_by(func.sum(FinancialEntry.amount).desc())
                .limit(top)
            ).all()
        ]

        largest = self._entry_rows(base.where(*filters).order_by(FinancialEntry.amount.desc()).limit(top))
        return SpendingSummary(total=Decimal(total), count=count, by_establishment=by_establishment, largest=largest)

    def bills_by_month(
        self,
        account_id: UUID,
        document_type: str | Sequence[str],
        *,
        since: date | None = None,
        until: date | None = None,
    ) -> list[EntryRow]:
        """Bills of the given type(s), in chronological order (no bounds = full history)."""
        types = [document_type] if isinstance(document_type, str) else list(document_type)
        stmt = (
            select(FinancialEntry)
            .join(Document, (Document.id == FinancialEntry.document_id) & (Document.account_id == FinancialEntry.account_id))
            .where(
                FinancialEntry.account_id == account_id,
                Document.document_type.in_(types),
                FinancialEntry.needs_review.is_(False),
            )
            .order_by(FinancialEntry.occurred_at)
        )
        if since:
            stmt = stmt.where(FinancialEntry.occurred_at >= since)
        if until:
            stmt = stmt.where(FinancialEntry.occurred_at <= until)
        return self._entry_rows(stmt)

    def _entry_rows(self, stmt) -> list[EntryRow]:
        stmt = stmt.with_only_columns(
            FinancialEntry.document_id,
            Document.original_filename,
            Document.document_type,
            FinancialEntry.description,
            FinancialEntry.establishment,
            FinancialEntry.amount,
            FinancialEntry.occurred_at,
            FinancialEntry.due_date,
            FinancialEntry.payment_status,
        )
        return [
            EntryRow(
                document_id=row[0],
                original_filename=row[1],
                document_type=row[2],
                description=row[3],
                establishment=row[4],
                amount=row[5],
                occurred_at=row[6],
                due_date=row[7],
                payment_status=str(row[8].value if hasattr(row[8], "value") else row[8]),
            )
            for row in self.session.execute(stmt).all()
        ]

    # -- itens ------------------------------------------------------------------

    def _item_filter(
        self, product: str, descriptions: Sequence[str] | None, seen: Sequence[str] | None = None
    ) -> ColumnElement[bool]:
        """Items chosen by exact name (`descriptions`, from the account vocabulary)
        or, without them, by text search on `product`. `seen`: names the choice evaluated;
        those left out of it (truncated vocabulary) still match by text search."""
        if descriptions is None:
            return _matches_product(DocumentItem.description, product)
        chosen = DocumentItem.description.in_(list(descriptions))
        if seen is None:
            return chosen
        return chosen | (_matches_product(DocumentItem.description, product) & DocumentItem.description.not_in(list(seen)))

    def item_vocabulary(
        self, account_id: UUID, *, start: date | None = None, end: date | None = None, limit: int = 400
    ) -> tuple[list[str], bool]:
        """Distinct item names of the account (as printed), most recently bought first.
        Returns (names, truncated?): `truncated` means there were more than `limit`."""
        last_seen = func.max(FinancialEntry.occurred_at)
        stmt = (
            select(DocumentItem.description)
            .join(
                FinancialEntry,
                (FinancialEntry.id == DocumentItem.financial_entry_id)
                & (FinancialEntry.account_id == DocumentItem.account_id),
            )
            .where(DocumentItem.account_id == account_id, FinancialEntry.needs_review.is_(False))
            .group_by(DocumentItem.description)
            .order_by(last_seen.desc(), DocumentItem.description)
            .limit(limit + 1)
        )
        if start:
            stmt = stmt.where(FinancialEntry.occurred_at >= start)
        if end:
            stmt = stmt.where(FinancialEntry.occurred_at <= end)
        names = list(self.session.scalars(stmt).all())
        return names[:limit], len(names) > limit

    def items_matching(
        self,
        account_id: UUID,
        product: str,
        *,
        start: date | None = None,
        end: date | None = None,
        order: str = "cheapest",
        limit: int = 20,
        descriptions: Sequence[str] | None = None,
        seen: Sequence[str] | None = None,
    ) -> list[ItemRow]:
        stmt = (
            select(
                DocumentItem.document_id,
                Document.original_filename,
                DocumentItem.description,
                FinancialEntry.establishment,
                FinancialEntry.occurred_at,
                DocumentItem.quantity,
                DocumentItem.unit,
                DocumentItem.unit_price,
                DocumentItem.total_price,
            )
            .join(
                FinancialEntry,
                (FinancialEntry.id == DocumentItem.financial_entry_id)
                & (FinancialEntry.account_id == DocumentItem.account_id),
            )
            .join(Document, (Document.id == DocumentItem.document_id) & (Document.account_id == DocumentItem.account_id))
            .where(
                DocumentItem.account_id == account_id,
                self._item_filter(product, descriptions, seen),
                FinancialEntry.needs_review.is_(False),
            )
        )
        if start:
            stmt = stmt.where(FinancialEntry.occurred_at >= start)
        if end:
            stmt = stmt.where(FinancialEntry.occurred_at <= end)

        if order == "latest":
            stmt = stmt.order_by(FinancialEntry.occurred_at.desc())
        else:
            stmt = stmt.order_by(DocumentItem.unit_price.asc().nulls_last(), FinancialEntry.occurred_at.desc())

        return [ItemRow(*row) for row in self.session.execute(stmt.limit(limit)).all()]

    def top_items(
        self,
        account_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        document_types: list[str] | None = None,
        establishment: str | None = None,
        limit: int = 10,
    ) -> list[ItemRow]:
        """Items with the highest value (line total), most expensive first."""
        stmt = (
            select(
                DocumentItem.document_id,
                Document.original_filename,
                DocumentItem.description,
                FinancialEntry.establishment,
                FinancialEntry.occurred_at,
                DocumentItem.quantity,
                DocumentItem.unit,
                DocumentItem.unit_price,
                DocumentItem.total_price,
            )
            .join(
                FinancialEntry,
                (FinancialEntry.id == DocumentItem.financial_entry_id)
                & (FinancialEntry.account_id == DocumentItem.account_id),
            )
            .join(Document, (Document.id == DocumentItem.document_id) & (Document.account_id == DocumentItem.account_id))
            .where(
                DocumentItem.account_id == account_id,
                DocumentItem.total_price.is_not(None),
                FinancialEntry.needs_review.is_(False),
            )
            .order_by(DocumentItem.total_price.desc(), FinancialEntry.occurred_at.desc())
            .limit(limit)
        )
        if start:
            stmt = stmt.where(FinancialEntry.occurred_at >= start)
        if end:
            stmt = stmt.where(FinancialEntry.occurred_at <= end)
        if document_types:
            stmt = stmt.where(_type_filter(account_id, document_types))
        if establishment:
            stmt = stmt.where(_contains(FinancialEntry.establishment, establishment))
        return [ItemRow(*row) for row in self.session.execute(stmt).all()]

    def top_products(
        self,
        account_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        document_types: list[str] | None = None,
        establishment: str | None = None,
        limit: int = 10,
        with_filenames: bool = True,
    ) -> list[ProductSpending]:
        """Spending summed per product (same description): 6 lines of "CSCOX LAR" become one."""
        total = func.sum(DocumentItem.total_price)
        stmt = (
            select(
                DocumentItem.description,
                func.count(DocumentItem.id),
                total,
                func.array_agg(func.distinct(DocumentItem.document_id)),
            )
            .join(
                FinancialEntry,
                (FinancialEntry.id == DocumentItem.financial_entry_id)
                & (FinancialEntry.account_id == DocumentItem.account_id),
            )
            .join(Document, (Document.id == DocumentItem.document_id) & (Document.account_id == DocumentItem.account_id))
            .where(
                DocumentItem.account_id == account_id,
                DocumentItem.total_price.is_not(None),
                FinancialEntry.needs_review.is_(False),
            )
            .group_by(DocumentItem.description)
            .order_by(total.desc(), DocumentItem.description)
            .limit(limit)
        )
        if start:
            stmt = stmt.where(FinancialEntry.occurred_at >= start)
        if end:
            stmt = stmt.where(FinancialEntry.occurred_at <= end)
        if document_types:
            stmt = stmt.where(_type_filter(account_id, document_types))
        if establishment:
            stmt = stmt.where(_contains(FinancialEntry.establishment, establishment))
        rows = self.session.execute(stmt).all()
        ids = {doc_id for *_, doc_ids in rows for doc_id in doc_ids}
        names = dict(
            self.session.execute(
                select(Document.id, Document.original_filename).where(
                    Document.account_id == account_id, Document.id.in_(ids)
                )
            ).all()
        ) if ids and with_filenames else {}
        return [
            ProductSpending(description, int(count), spent, [(i, names.get(i, "documento")) for i in doc_ids])
            for description, count, spent, doc_ids in rows
        ]

    def product_totals(
        self,
        account_id: UUID,
        product: str,
        *,
        start: date | None = None,
        end: date | None = None,
        descriptions: Sequence[str] | None = None,
        seen: Sequence[str] | None = None,
    ) -> ProductTotals:
        """How much was spent on a product (sum of items), across every purchase in the filter."""
        stmt = (
            select(DocumentItem.unit, func.count(), func.coalesce(func.sum(DocumentItem.total_price), 0), func.sum(DocumentItem.quantity))
            .join(
                FinancialEntry,
                (FinancialEntry.id == DocumentItem.financial_entry_id)
                & (FinancialEntry.account_id == DocumentItem.account_id),
            )
            .where(
                DocumentItem.account_id == account_id,
                self._item_filter(product, descriptions, seen),
                FinancialEntry.needs_review.is_(False),
            )
            .group_by(DocumentItem.unit)
        )
        if start:
            stmt = stmt.where(FinancialEntry.occurred_at >= start)
        if end:
            stmt = stmt.where(FinancialEntry.occurred_at <= end)
        rows = self.session.execute(stmt).all()
        return ProductTotals(
            purchases=sum(n for _, n, _, _ in rows),
            total_spent=sum((Decimal(total) for _, _, total, _ in rows), Decimal("0")),
            quantity_by_unit={(unit or "UN").upper(): qty for unit, _, _, qty in rows if qty is not None},
        )

    # -- documents -------------------------------------------------------------

    def pending_review_count(self, account_id: UUID) -> int:
        """Receipts with a doubtful reading that are not in the numbers yet."""
        return self.session.scalar(
            select(func.count(func.distinct(FinancialEntry.document_id))).where(
                FinancialEntry.account_id == account_id, FinancialEntry.needs_review.is_(True)
            )
        ) or 0

    def list_documents(
        self,
        account_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        document_types: list[str] | None = None,
        establishment: str | None = None,
        limit: int = 20,
    ) -> list[DocumentRow]:
        reference_date = func.coalesce(FinancialEntry.occurred_at, cast(Document.created_at, Date))
        stmt = (
            select(
                Document.id,
                Document.original_filename,
                Document.document_type,
                Document.processing_status,
                Document.created_at,
                FinancialEntry.occurred_at,
                FinancialEntry.amount,
                FinancialEntry.establishment,
            )
            .outerjoin(
                FinancialEntry,
                (FinancialEntry.document_id == Document.id) & (FinancialEntry.account_id == Document.account_id),
            )
            .where(Document.account_id == account_id)
            # Most recent upload first ("last receipt I sent"); the period filters by the document date.
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        if start:
            stmt = stmt.where(reference_date >= start)
        if end:
            stmt = stmt.where(reference_date <= end)
        if document_types:
            stmt = stmt.where(_type_filter(account_id, document_types))
        if establishment:
            stmt = stmt.where(_contains(FinancialEntry.establishment, establishment))

        return [
            DocumentRow(
                document_id=row[0],
                original_filename=row[1],
                document_type=row[2],
                processing_status=str(row[3].value if hasattr(row[3], "value") else row[3]),
                created_at=row[4],
                occurred_at=row[5],
                amount=row[6],
                establishment=row[7],
            )
            for row in self.session.execute(stmt).all()
        ]

    def items_of_documents(self, account_id: UUID, document_ids: Sequence[UUID]) -> dict[UUID, list[DocumentItem]]:
        """Items of each document (the position on the receipt is not stored: stable order, not the receipt's)."""
        if not document_ids:
            return {}
        rows = self.session.scalars(
            select(DocumentItem)
            .where(DocumentItem.account_id == account_id, DocumentItem.document_id.in_(list(document_ids)))
            .order_by(DocumentItem.document_id, DocumentItem.created_at, DocumentItem.id)
        ).all()
        by_document: dict[UUID, list[DocumentItem]] = {}
        for item in rows:
            by_document.setdefault(item.document_id, []).append(item)
        return by_document

    def price_series(
        self,
        account_id: UUID,
        *,
        start: date | None = None,
        end: date | None = None,
        document_types: list[str] | None = None,
        establishment: str | None = None,
        limit: int = 3000,
    ) -> list[ItemRow]:
        """Items with a unit price, oldest to newest (basis for "what got more expensive")."""
        stmt = (
            select(
                DocumentItem.document_id,
                Document.original_filename,
                DocumentItem.description,
                FinancialEntry.establishment,
                FinancialEntry.occurred_at,
                DocumentItem.quantity,
                DocumentItem.unit,
                DocumentItem.unit_price,
                DocumentItem.total_price,
            )
            .join(
                FinancialEntry,
                (FinancialEntry.id == DocumentItem.financial_entry_id)
                & (FinancialEntry.account_id == DocumentItem.account_id),
            )
            .join(Document, (Document.id == DocumentItem.document_id) & (Document.account_id == DocumentItem.account_id))
            .where(
                DocumentItem.account_id == account_id,
                DocumentItem.unit_price.is_not(None),
                DocumentItem.unit_price > 0,
                FinancialEntry.needs_review.is_(False),
            )
            .order_by(FinancialEntry.occurred_at.desc(), DocumentItem.id)
            .limit(limit)
        )
        if start:
            stmt = stmt.where(FinancialEntry.occurred_at >= start)
        if end:
            stmt = stmt.where(FinancialEntry.occurred_at <= end)
        if document_types:
            stmt = stmt.where(_type_filter(account_id, document_types))
        if establishment:
            stmt = stmt.where(_contains(FinancialEntry.establishment, establishment))
        # Most recent first within the limit; returned in chronological order.
        return [ItemRow(*row) for row in reversed(self.session.execute(stmt).all())]


# -- reports (dashboard) -------------------------------------------------------


@dataclass(frozen=True)
class MonthTotal:
    month: date
    total: Decimal
    count: int


@dataclass(frozen=True)
class GroupTotal:
    key: str | None
    label: str | None
    total: Decimal
    count: int


@dataclass(frozen=True)
class PriceStats:
    count: int
    min_unit_price: Decimal | None
    max_unit_price: Decimal | None
    avg_unit_price: Decimal | None


class ReportQueryRepository(FinancialQueryRepository):
    """Dashboard aggregations. Same spending filters as the assistant."""

    def _expenses(self, *columns):
        return select(*columns).select_from(FinancialEntry).outerjoin(
            Document, (Document.id == FinancialEntry.document_id) & (Document.account_id == FinancialEntry.account_id)
        )

    def monthly_totals(self, account_id: UUID, start: date, end: date) -> list[MonthTotal]:
        month = cast(func.date_trunc("month", FinancialEntry.occurred_at), Date)
        stmt = (
            self._expenses(month, func.sum(FinancialEntry.amount), func.count())
            .where(*self._expense_filters(account_id, start, end, None, None))
            .group_by(month)
            .order_by(month)
        )
        return [MonthTotal(month=m, total=t, count=n) for m, t, n in self.session.execute(stmt).all()]

    def totals_by_category(self, account_id: UUID, start: date, end: date) -> list[GroupTotal]:
        stmt = (
            self._expenses(Category.id, Category.name, func.sum(FinancialEntry.amount), func.count())
            .outerjoin(
                Category,
                (Category.id == FinancialEntry.category_id) & (Category.account_id == FinancialEntry.account_id),
            )
            .where(*self._expense_filters(account_id, start, end, None, None))
            .group_by(Category.id, Category.name)
            .order_by(func.sum(FinancialEntry.amount).desc())
        )
        return [
            GroupTotal(key=str(cid) if cid else None, label=name, total=t, count=n)
            for cid, name, t, n in self.session.execute(stmt).all()
        ]

    def totals_by_document_type(self, account_id: UUID, start: date, end: date) -> list[GroupTotal]:
        stmt = (
            self._expenses(Document.document_type, func.sum(FinancialEntry.amount), func.count())
            .where(*self._expense_filters(account_id, start, end, None, None))
            .group_by(Document.document_type)
            .order_by(func.sum(FinancialEntry.amount).desc())
        )
        return [
            GroupTotal(key=doc_type, label=None, total=t, count=n)
            for doc_type, t, n in self.session.execute(stmt).all()
        ]

    def item_price_stats(self, account_id: UUID, product: str, start: date | None, end: date | None) -> PriceStats:
        stmt = (
            select(
                func.count(),
                func.min(DocumentItem.unit_price),
                func.max(DocumentItem.unit_price),
                func.avg(DocumentItem.unit_price),
            )
            .select_from(DocumentItem)
            .join(
                FinancialEntry,
                (FinancialEntry.id == DocumentItem.financial_entry_id)
                & (FinancialEntry.account_id == DocumentItem.account_id),
            )
            .where(
                DocumentItem.account_id == account_id,
                DocumentItem.unit_price.is_not(None),
                _matches_product(DocumentItem.description, product),
                FinancialEntry.needs_review.is_(False),
            )
        )
        if start:
            stmt = stmt.where(FinancialEntry.occurred_at >= start)
        if end:
            stmt = stmt.where(FinancialEntry.occurred_at <= end)
        count, low, high, avg = self.session.execute(stmt).one()
        return PriceStats(
            count=count,
            min_unit_price=low,
            max_unit_price=high,
            avg_unit_price=avg.quantize(Decimal("0.0001")) if avg is not None else None,
        )
