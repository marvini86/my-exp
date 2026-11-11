from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from models.category import Category


class CategoryRepository:
    """Category access.

    Every query explicitly filters by `account_id` on top of RLS:
    RLS is the last barrier, not the only one.
    """

    def __init__(self, session: Session):
        self.session = session

    def list_by_account(self, account_id: UUID) -> list[Category]:
        stmt = (
            select(Category)
            .where(Category.account_id == account_id)
            .order_by(Category.name)
        )
        return list(self.session.scalars(stmt).all())

    def get(self, account_id: UUID, category_id: UUID) -> Category | None:
        stmt = select(Category).where(Category.account_id == account_id, Category.id == category_id)
        return self.session.scalars(stmt).one_or_none()

    def get_by_name(self, account_id: UUID, name: str) -> Category | None:
        stmt = select(Category).where(
            Category.account_id == account_id,
            Category.name == name,
        )
        return self.session.scalars(stmt).one_or_none()

    def get_or_create(self, account_id: UUID, name: str) -> UUID:
        """Category id by name, creating it if it does not exist.

        ON CONFLICT: two workers categorizing at the same time neither collide
        (uq_category_name) nor abort the extraction transaction.
        """
        self.session.execute(
            insert(Category)
            .values(account_id=account_id, name=name)
            .on_conflict_do_nothing(index_elements=[Category.account_id, Category.name])
        )
        category_id = self.session.scalar(
            select(Category.id).where(Category.account_id == account_id, Category.name == name)
        )
        assert category_id is not None
        return category_id

    def add(self, category: Category) -> Category:
        self.session.add(category)
        self.session.flush()
        return category
