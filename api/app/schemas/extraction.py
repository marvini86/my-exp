"""Financial extraction contract.

Every extractor (NF-e XML, LLM for PDF/image) returns `ExtractedDocument`.
Validation and persistence depend only on this contract, never on the extractor.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from models.financial import PaymentStatus

Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
Quantity = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)]
UnitPrice = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class DocumentType(StrEnum):
    NFCE = "nfce"  # electronic consumer receipt (supermarket, pharmacy...)
    NFE = "nfe"  # electronic invoice (goods)
    NFSE = "nfse"  # service invoice
    GROCERY_RECEIPT = "grocery_receipt"
    PHARMACY_RECEIPT = "pharmacy_receipt"
    ENERGY_BILL = "energy_bill"
    WATER_BILL = "water_bill"
    TELECOM_BILL = "telecom_bill"  # phone, mobile, internet, pay TV
    BANK_SLIP = "bank_slip"  # boleto
    PAYMENT_RECEIPT = "payment_receipt"
    OTHER = "other"


class SpendingCategory(StrEnum):
    """Default categories, assigned automatically (no manual registration)."""

    GROCERIES = "mercado"
    HEALTH = "saude"
    HOME_BILLS = "contas_casa"
    TRANSPORT = "transporte"
    EATING_OUT = "alimentacao_fora"
    SHOPPING = "compras"
    SERVICES = "servicos"
    OTHER = "outros"


# Display name = name of the row in `categories` (created in the account on first use).
SPENDING_CATEGORY_NAMES: dict[SpendingCategory, str] = {
    SpendingCategory.GROCERIES: "Mercado",
    SpendingCategory.HEALTH: "Saúde e farmácia",
    SpendingCategory.HOME_BILLS: "Contas da casa",
    SpendingCategory.TRANSPORT: "Transporte",
    SpendingCategory.EATING_OUT: "Restaurantes e lanches",
    SpendingCategory.SHOPPING: "Compras",
    SpendingCategory.SERVICES: "Serviços",
    SpendingCategory.OTHER: "Outros",
}


class ExtractedItem(BaseModel):
    description: LongText
    brand: ShortText | None = None
    sku: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    quantity: Quantity | None = None
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)] | None = None
    unit_price: UnitPrice | None = None
    total_price: Money | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedDocument(BaseModel):
    document_type: DocumentType
    description: LongText | None = None
    establishment: ShortText | None = None
    establishment_tax_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] | None = None

    issued_at: date | None = None
    due_date: date | None = None
    paid_at: datetime | None = None
    payment_status: PaymentStatus = PaymentStatus.UNKNOWN

    total_amount: Money | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] = "BRL"

    items: list[ExtractedItem] = Field(default_factory=list)
    # Extractor (LLM) suggestion; the categorizer makes the final call.
    category: SpendingCategory | None = None
    # Type-specific data (access key, kWh consumption, etc.).
    metadata: dict[str, Any] = Field(default_factory=dict)
