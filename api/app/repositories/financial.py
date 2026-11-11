from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from models.financial import DocumentItem, FinancialEntry


class FinancialRepository:
    """Entries and items. Filters by `account_id` on top of RLS."""

    def __init__(self, session: Session):
        self.session = session

    def delete_by_document(self, account_id: UUID, document_id: UUID) -> None:
        """Removes what a previous extraction produced (idempotent reprocessing)."""
        self.session.execute(
            delete(DocumentItem).where(
                DocumentItem.account_id == account_id,
                DocumentItem.document_id == document_id,
            )
        )
        self.session.execute(
            delete(FinancialEntry).where(
                FinancialEntry.account_id == account_id,
                FinancialEntry.document_id == document_id,
            )
        )

    def establishment_name_for_tax_id(self, account_id: UUID, tax_id: str) -> str | None:
        """Name already used in the account for this CNPJ (the most recent one)."""
        stmt = (
            select(FinancialEntry.establishment)
            .where(
                FinancialEntry.account_id == account_id,
                FinancialEntry.metadata_["establishment_tax_id"].astext == tax_id,
                FinancialEntry.establishment.is_not(None),
            )
            .order_by(FinancialEntry.created_at.desc())
            .limit(1)
        )
        return self.session.scalar(stmt)

    def rename_establishment(self, account_id: UUID, tax_id: str, name: str) -> None:
        """Standardizes the name of every entry in the account with this CNPJ."""
        self.session.execute(
            update(FinancialEntry)
            .where(
                FinancialEntry.account_id == account_id,
                FinancialEntry.metadata_["establishment_tax_id"].astext == tax_id,
            )
            .values(establishment=name)
        )

    def possible_duplicate(
        self, account_id: UUID, tax_id: str, occurred_at: date, amount: Decimal, exclude_document_id: UUID
    ) -> UUID | None:
        """Receipt without an access key: another one with the same tax ID, date and amount is probably the same."""
        return self.session.scalars(
            select(FinancialEntry.document_id).where(
                FinancialEntry.account_id == account_id,
                FinancialEntry.metadata_["establishment_tax_id"].astext == tax_id,
                FinancialEntry.occurred_at == occurred_at,
                FinancialEntry.amount == amount,
                FinancialEntry.document_id.is_not(None),
                FinancialEntry.document_id != exclude_document_id,
            )
        ).first()

    def confirm_review(self, account_id: UUID, document_id: UUID) -> None:
        """Amounts checked by someone in the account: the entry goes back into the totals."""
        self.session.execute(
            update(FinancialEntry)
            .where(FinancialEntry.account_id == account_id, FinancialEntry.document_id == document_id)
            .values(needs_review=False)
        )

    def add_entry(self, entry: FinancialEntry, items: list[DocumentItem]) -> FinancialEntry:
        self.session.add(entry)
        self.session.flush()

        for item in items:
            item.financial_entry_id = entry.id
        self.session.add_all(items)
        self.session.flush()

        return entry

    def list_entries_by_document(self, account_id: UUID, document_id: UUID) -> list[FinancialEntry]:
        stmt = select(FinancialEntry).where(
            FinancialEntry.account_id == account_id,
            FinancialEntry.document_id == document_id,
        )
        return list(self.session.scalars(stmt).all())

    def list_items_by_document(self, account_id: UUID, document_id: UUID) -> list[DocumentItem]:
        stmt = (
            select(DocumentItem)
            .where(DocumentItem.account_id == account_id, DocumentItem.document_id == document_id)
            .order_by(DocumentItem.created_at, DocumentItem.description)
        )
        return list(self.session.scalars(stmt).all())
