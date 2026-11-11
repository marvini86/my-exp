from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from core.account_context import AccountContext, get_account_context
from database.connection import get_db
from models.financial import EntryType, PaymentStatus
from repositories.category import CategoryRepository
from repositories.financial_entry import EntryFilters, FinancialEntryRepository
from repositories.financial_queries import ReportQueryRepository
from schemas.financial import EntryPage, EntryResponse, ItemPricesResponse, SummaryResponse, UpdateEntryRequest
from services.financial import FinancialService

router = APIRouter(prefix="/accounts/{account_id}")


def get_financial_service(session: Session = Depends(get_db)) -> FinancialService:
    return FinancialService(
        session, FinancialEntryRepository(session), ReportQueryRepository(session), CategoryRepository(session)
    )


@router.get("/entries/", response_model=EntryPage, tags=["Entries"])
def list_entries(
    context: AccountContext = Depends(get_account_context),
    service: FinancialService = Depends(get_financial_service),
    start: date | None = None,
    end: date | None = None,
    entry_type: EntryType | None = None,
    document_type: Annotated[str | None, Query(max_length=50)] = None,
    payment_status: PaymentStatus | None = None,
    category_id: UUID | None = None,
    uncategorized: bool = False,
    q: Annotated[str | None, Query(max_length=100, description="Description or store")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EntryPage:
    """Entries, from the most recent to the oldest."""
    filters = EntryFilters(
        start=start,
        end=end,
        entry_type=entry_type,
        document_type=document_type,
        payment_status=payment_status,
        category_id=category_id,
        uncategorized=uncategorized,
        q=q,
    )
    return service.list_entries(context, filters, limit, offset)


@router.get("/entries/{entry_id}", response_model=EntryResponse, tags=["Entries"])
def get_entry(
    entry_id: UUID,
    context: AccountContext = Depends(get_account_context),
    service: FinancialService = Depends(get_financial_service),
) -> EntryResponse:
    return service.get_entry(context, entry_id)


@router.patch("/entries/{entry_id}", response_model=EntryResponse, tags=["Entries"])
def update_entry(
    entry_id: UUID,
    request: UpdateEntryRequest,
    context: AccountContext = Depends(get_account_context),
    service: FinancialService = Depends(get_financial_service),
) -> EntryResponse:
    """Sets or removes (`null`) the entry's category."""
    return service.update_entry(context, entry_id, request)


@router.get("/reports/summary", response_model=SummaryResponse, tags=["Reports"])
def spending_summary(
    context: AccountContext = Depends(get_account_context),
    service: FinancialService = Depends(get_financial_service),
    start: date | None = None,
    end: date | None = None,
) -> SummaryResponse:
    """Dashboard. Without dates: current month + the 5 previous ones. At most 3 years."""
    return service.summary(context, start, end)


@router.get("/items/", response_model=ItemPricesResponse, tags=["Products"])
def item_prices(
    q: Annotated[str, Query(min_length=2, max_length=100, description="Product, e.g. leite (milk)")],
    context: AccountContext = Depends(get_account_context),
    service: FinancialService = Depends(get_financial_service),
    order: Literal["cheapest", "latest"] = "cheapest",
    start: date | None = None,
    end: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> ItemPricesResponse:
    """Price history of a product across the account's purchases (accent insensitive)."""
    return service.item_prices(context, q, order, start, end, limit)
