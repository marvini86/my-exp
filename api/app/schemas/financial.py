from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from models.financial import EntryType, PaymentStatus


class EntryCategory(BaseModel):
    id: UUID
    name: str


class EntryResponse(BaseModel):
    id: UUID
    document_id: UUID | None
    document_filename: str | None
    document_type: str | None
    category: EntryCategory | None
    entry_type: EntryType
    description: str
    establishment: str | None
    # Decimal becomes a string in JSON ("63.43"): no cents lost on the client.
    amount: Decimal
    currency: str
    occurred_at: date
    due_date: date | None
    paid_at: datetime | None
    payment_status: PaymentStatus
    # Doubtful reading: shown in lists, but left out of every total until confirmed.
    needs_review: bool = False


class EntryPage(BaseModel):
    items: list[EntryResponse]
    total: int
    limit: int
    offset: int
    # Sum of the expenses with the same filters (all pages), computed in the database.
    total_expenses: Decimal


class UpdateEntryRequest(BaseModel):
    # null remove a categoria.
    category_id: UUID | None


class Period(BaseModel):
    start: date
    end: date


class MonthTotalResponse(BaseModel):
    month: str  # "2026-10"
    total: Decimal
    count: int


class GroupTotalResponse(BaseModel):
    key: str | None
    label: str
    total: Decimal
    count: int


class SummaryResponse(BaseModel):
    """Spending (entry_type=expense) in the period."""

    period: Period
    total: Decimal
    count: int
    by_month: list[MonthTotalResponse]
    by_category: list[GroupTotalResponse]
    by_establishment: list[GroupTotalResponse]
    by_document_type: list[GroupTotalResponse]
    # Items summed by printed name (count = how many times it appears on receipts).
    top_products: list[GroupTotalResponse]
    # Unpaid, overdue in the last 30 days or due in the next 30.
    open_bills: list[EntryResponse]
    # Receipts with a doubtful reading that are not in the numbers yet.
    pending_review: int = 0


class PriceStatsResponse(BaseModel):
    count: int
    min_unit_price: Decimal | None
    max_unit_price: Decimal | None
    avg_unit_price: Decimal | None


class ItemPriceResponse(BaseModel):
    document_id: UUID
    document_filename: str
    description: str
    establishment: str | None
    occurred_at: date
    quantity: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    total_price: Decimal | None


class ItemPricesResponse(BaseModel):
    query: str
    stats: PriceStatsResponse
    items: list[ItemPriceResponse]
