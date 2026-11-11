import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CHAR,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.base import Base
from database.mixins.timestamped import Timestamped
from database.mixins.uuid_primary_key import UUIDPrimaryKey


class EntryType(StrEnum):
    EXPENSE = "expense"
    INCOME = "income"
    REFUND = "refund"
    TRANSFER = "transfer"


class PaymentStatus(StrEnum):
    UNKNOWN = "unknown"
    PENDING = "pending"
    PAID = "paid"
    OVERDUE = "overdue"
    CANCELLED = "cancelled"


def _varchar_enum(enum_cls: type[StrEnum]) -> Enum:
    # VARCHAR + CHECK in the database; no native PostgreSQL enum.
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=30,
        values_callable=lambda members: [member.value for member in members],
    )


def _metadata_column() -> Any:
    # "metadata" is reserved by SQLAlchemy; the Python attribute is `metadata_`.
    return mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


class FinancialEntry(UUIDPrimaryKey, Timestamped, Base):
    """Financial entry. RLS-protected table."""

    __tablename__ = "financial_entries"
    __table_args__ = (
        UniqueConstraint("account_id", "id", name="uq_financial_entries_account_id"),
        ForeignKeyConstraint(
            ["account_id", "document_id"],
            ["documents.account_id", "documents.id"],
            name="fk_financial_entries_document",
        ),
        ForeignKeyConstraint(
            ["account_id", "category_id"],
            ["categories.account_id", "categories.id"],
            name="fk_financial_entries_category",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))
    category_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    entry_type: Mapped[EntryType] = mapped_column(
        _varchar_enum(EntryType),
        nullable=False,
        default=EntryType.EXPENSE,
        server_default=EntryType.EXPENSE.value,
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    establishment: Mapped[str | None] = mapped_column(String(255))

    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="BRL", server_default="BRL")

    occurred_at: Mapped[date] = mapped_column(nullable=False)
    due_date: Mapped[date | None] = mapped_column()
    paid_at: Mapped[datetime | None] = mapped_column()

    payment_status: Mapped[PaymentStatus] = mapped_column(
        _varchar_enum(PaymentStatus),
        nullable=False,
        default=PaymentStatus.UNKNOWN,
        server_default=PaymentStatus.UNKNOWN.value,
    )
    # Doubtful reading: left out of the totals until someone confirms it (POST .../documents/{id}/review).
    needs_review: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=text("false"))
    metadata_: Mapped[dict[str, Any]] = _metadata_column()


class DocumentItem(UUIDPrimaryKey, Timestamped, Base):
    """Item of a document (e.g. a product on a receipt). RLS-protected table."""

    __tablename__ = "document_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["account_id", "document_id"],
            ["documents.account_id", "documents.id"],
            name="fk_document_items_document",
        ),
        ForeignKeyConstraint(
            ["account_id", "financial_entry_id"],
            ["financial_entries.account_id", "financial_entries.id"],
            name="fk_document_items_entry",
        ),
    )

    account_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("accounts.id"), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    financial_entry_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    description: Mapped[str] = mapped_column(String(500), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    sku: Mapped[str | None] = mapped_column(String(100))

    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    unit: Mapped[str | None] = mapped_column(String(30))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    metadata_: Mapped[dict[str, Any]] = _metadata_column()
