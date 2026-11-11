"""Financial reads for the frontend: entries, dashboard and prices.

Every number comes from SQL aggregations (never from the LLM or the client).
"""

from collections.abc import Callable
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from core.account_context import AccountContext
from core.exceptions import AppError, NotFoundError
from repositories.category import CategoryRepository
from repositories.financial_entry import EntryFilters, EntryListRow, FinancialEntryRepository
from repositories.financial_queries import GroupTotal, ReportQueryRepository
from schemas.financial import (
    EntryCategory,
    EntryPage,
    EntryResponse,
    GroupTotalResponse,
    ItemPriceResponse,
    ItemPricesResponse,
    MonthTotalResponse,
    Period,
    PriceStatsResponse,
    SummaryResponse,
    UpdateEntryRequest,
)
from services.indexing import DOCUMENT_TYPE_LABELS

UNCATEGORIZED = "Sem categoria"
UNKNOWN_ESTABLISHMENT = "(não informado)"
DEFAULT_SUMMARY_MONTHS = 6
MAX_SUMMARY_DAYS = 3 * 366
TOP_PRODUCTS = 5
OPEN_BILLS_WINDOW = timedelta(days=30)


class InvalidPeriodError(AppError):
    """400: inconsistent period (422 is reserved for Pydantic format errors)."""


def default_summary_start(today: date, months: int = DEFAULT_SUMMARY_MONTHS) -> date:
    """1st day of the month, `months - 1` months ago (6 → current month + the 5 previous ones)."""
    index = today.year * 12 + today.month - 1 - (months - 1)
    return date(index // 12, index % 12 + 1, 1)


class FinancialService:
    def __init__(
        self,
        session: Session,
        entries: FinancialEntryRepository,
        reports: ReportQueryRepository,
        categories: CategoryRepository,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.session = session
        self.entries = entries
        self.reports = reports
        self.categories = categories
        self.today = today

    # -- entries -------------------------------------------------------------

    def list_entries(self, context: AccountContext, filters: EntryFilters, limit: int, offset: int) -> EntryPage:
        _check_period(filters.start, filters.end)
        rows, total = self.entries.list_page(context.account_id, filters, limit, offset)
        return EntryPage(
            items=[entry_response(r) for r in rows],
            total=total,
            limit=limit,
            offset=offset,
            total_expenses=self.entries.expense_total(context.account_id, filters),
        )

    def get_entry(self, context: AccountContext, entry_id: UUID) -> EntryResponse:
        row = self.entries.get_row(context.account_id, entry_id)
        if row is None:
            raise NotFoundError("Entry not found")
        return entry_response(row)

    def update_entry(self, context: AccountContext, entry_id: UUID, request: UpdateEntryRequest) -> EntryResponse:
        """Any member can categorize: it is the family's day-to-day use."""
        entry = self.entries.get_for_update(context.account_id, entry_id)
        if entry is None:
            raise NotFoundError("Entry not found")

        if request.category_id is not None and self.categories.get(context.account_id, request.category_id) is None:
            raise NotFoundError("Category not found")

        entry.category_id = request.category_id
        self.session.commit()
        return self.get_entry(context, entry_id)

    # -- painel -------------------------------------------------------------------

    def summary(self, context: AccountContext, start: date | None, end: date | None) -> SummaryResponse:
        today = self.today()
        end = end or today
        start = start or default_summary_start(end)
        _check_period(start, end)
        if (end - start).days > MAX_SUMMARY_DAYS:
            raise InvalidPeriodError("Period too long (max 3 years)")

        account_id = context.account_id
        overview = self.reports.spending_summary(account_id, start=start, end=end, top=10)

        return SummaryResponse(
            period=Period(start=start, end=end),
            total=overview.total,
            count=overview.count,
            by_month=[
                MonthTotalResponse(month=m.month.strftime("%Y-%m"), total=m.total, count=m.count)
                for m in self.reports.monthly_totals(account_id, start, end)
            ],
            by_category=[
                _group(g, g.label or UNCATEGORIZED) for g in self.reports.totals_by_category(account_id, start, end)
            ],
            by_establishment=[
                GroupTotalResponse(key=e.establishment, label=e.establishment, total=e.total, count=e.count)
                for e in overview.by_establishment
            ],
            by_document_type=[
                _group(g, DOCUMENT_TYPE_LABELS.get(g.key or "", "Lançamento manual" if g.key is None else "Documento"))
                for g in self.reports.totals_by_document_type(account_id, start, end)
            ],
            top_products=[
                GroupTotalResponse(key=p.description, label=p.description, total=p.total_spent, count=p.purchases)
                for p in self.reports.top_products(
                    account_id, start=start, end=end, limit=TOP_PRODUCTS, with_filenames=False
                )
            ],
            open_bills=[
                entry_response(r)
                for r in self.entries.open_bills(account_id, today - OPEN_BILLS_WINDOW, today + OPEN_BILLS_WINDOW)
            ],
        )

    # -- prices -------------------------------------------------------------------

    def item_prices(
        self,
        context: AccountContext,
        q: str,
        order: str,
        start: date | None,
        end: date | None,
        limit: int,
    ) -> ItemPricesResponse:
        _check_period(start, end)
        rows = self.reports.items_matching(context.account_id, q, start=start, end=end, order=order, limit=limit)
        stats = self.reports.item_price_stats(context.account_id, q, start, end)
        return ItemPricesResponse(
            query=q,
            stats=PriceStatsResponse(**stats.__dict__),
            items=[
                ItemPriceResponse(
                    document_id=r.document_id,
                    document_filename=r.original_filename,
                    description=r.description,
                    establishment=r.establishment,
                    occurred_at=r.occurred_at,
                    quantity=r.quantity,
                    unit=r.unit,
                    unit_price=r.unit_price,
                    total_price=r.total_price,
                )
                for r in rows
            ],
        )


def _check_period(start: date | None, end: date | None) -> None:
    if start and end and start > end:
        raise InvalidPeriodError("start must be before end")


def _group(g: GroupTotal, label: str) -> GroupTotalResponse:
    return GroupTotalResponse(key=g.key, label=label, total=g.total, count=g.count)


def entry_response(row: EntryListRow) -> EntryResponse:
    e = row.entry
    return EntryResponse(
        id=e.id,
        document_id=e.document_id,
        document_filename=row.original_filename,
        document_type=row.document_type,
        category=EntryCategory(id=e.category_id, name=row.category_name) if e.category_id and row.category_name else None,
        entry_type=e.entry_type,
        description=e.description,
        establishment=e.establishment,
        amount=e.amount,
        currency=e.currency,
        occurred_at=e.occurred_at,
        due_date=e.due_date,
        paid_at=e.paid_at,
        payment_status=e.payment_status,
    )

