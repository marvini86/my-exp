from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.account_context import AccountContext
from core.exceptions import ConflictError
from models.account_member import AccountRole
from models.category import Category
from repositories.category import CategoryRepository
from schemas.category import CategoryResponse, CreateCategoryRequest
from services.account_access import require_role

CATEGORY_MANAGERS = (AccountRole.OWNER, AccountRole.ADMIN)


class CategoryService:
    def __init__(self, session: Session, category_repository: CategoryRepository) -> None:
        self.session = session
        self.category_repository = category_repository

    def list_categories(self, context: AccountContext) -> list[CategoryResponse]:
        return [
            CategoryResponse.model_validate(category)
            for category in self.category_repository.list_by_account(context.account_id)
        ]

    def create_category(self, context: AccountContext, request: CreateCategoryRequest) -> CategoryResponse:
        require_role(context.role, *CATEGORY_MANAGERS)

        if self.category_repository.get_by_name(context.account_id, request.name) is not None:
            raise ConflictError("Category already exists")

        category = Category(
            account_id=context.account_id,
            name=request.name,
            description=request.description,
        )

        try:
            self.category_repository.add(category)
            self.session.commit()
        except IntegrityError as exc:
            # Race with another creation with the same name (uq_category_name).
            self.session.rollback()
            raise ConflictError("Category already exists") from exc

        return CategoryResponse.model_validate(category)
