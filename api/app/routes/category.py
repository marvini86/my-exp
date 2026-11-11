from http import HTTPStatus

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.account_context import AccountContext, get_account_context
from database.connection import get_db
from repositories.category import CategoryRepository
from schemas.category import CategoryResponse, CreateCategoryRequest
from services.category import CategoryService

router = APIRouter(prefix="/accounts/{account_id}/categories", tags=["Categories"])


def get_category_service(session: Session = Depends(get_db)) -> CategoryService:
    return CategoryService(session, CategoryRepository(session))


@router.get("/", response_model=list[CategoryResponse])
def list_categories(
    context: AccountContext = Depends(get_account_context),
    service: CategoryService = Depends(get_category_service),
) -> list[CategoryResponse]:
    return service.list_categories(context)


@router.post("/", response_model=CategoryResponse, status_code=HTTPStatus.CREATED)
def create_category(
    request: CreateCategoryRequest,
    context: AccountContext = Depends(get_account_context),
    service: CategoryService = Depends(get_category_service),
) -> CategoryResponse:
    return service.create_category(context, request)
