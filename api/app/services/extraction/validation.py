"""Business validation of the extraction result.

Types and limits are already guaranteed by the Pydantic schema; this holds rules
that depend on several fields. Inconsistencies become warnings (the entry is
written and the warnings stay in `documents.extracted_data`); only the complete
absence of values prevents the entry.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from schemas.extraction import DocumentType, ExtractedDocument
from services.document_processor import PermanentProcessingError

# Tolerance for rounding between the items and the total.
_TOTAL_TOLERANCE = Decimal("0.05")

# Documents whose items ARE the purchase (the sum must match the total).
_ITEMIZED_PURCHASES = frozenset(
    {DocumentType.NFCE, DocumentType.NFE, DocumentType.GROCERY_RECEIPT, DocumentType.PHARMACY_RECEIPT}
)


@dataclass
class ValidationResult:
    amount: Decimal
    warnings: list[str] = field(default_factory=list)


def _discount(extracted: ExtractedDocument) -> Decimal:
    try:
        return abs(Decimal(str(extracted.metadata.get("discount") or "0")))
    except ArithmeticError:
        return Decimal("0")


def items_total_difference(extracted: ExtractedDocument) -> Decimal | None:
    """Difference between the items sum and the total, accepting the printed discount
    ("VALOR TOTAL 115,93 · DESCONTO 3,78 · A PAGAR 112,15")."""
    priced = [item.total_price for item in extracted.items if item.total_price is not None]
    if not priced or extracted.total_amount is None:
        return None
    items_total = sum(priced, Decimal("0"))
    return min(abs(items_total - extracted.total_amount), abs(items_total - _discount(extracted) - extracted.total_amount))


def items_count_matches(extracted: ExtractedDocument) -> bool:
    """"Qtd. total de itens" (total item count) may count LINES or UNITS (2 units of a product count 2;
    a weighed item counts 1). Either count is accepted."""
    printed = str(extracted.metadata.get("items_count_printed") or "")
    if not printed.isdigit():
        return True
    units = 0
    for item in extracted.items:
        quantity = item.quantity
        whole = quantity is not None and quantity == quantity.to_integral_value() and quantity > 0
        units += int(quantity) if whole else 1
    return int(printed) in (len(extracted.items), units)


def review_reasons(extracted: ExtractedDocument) -> list[dict[str, str]]:
    """Reading still doubtful after the re-reads: the entry stays pending review
    (out of the totals). Stable codes + values; the interface builds the message.

    Only for purchase receipts: on a bill (water, electricity...) the items are a partial breakdown
    (consumption tiers, without sewage/fees) and the printed total is what counts; removing the bill
    from the totals and from the bills to pay because of that would do more harm than good."""
    reasons: list[dict[str, str]] = []
    if extracted.document_type not in _ITEMIZED_PURCHASES:
        return reasons
    difference = items_total_difference(extracted)
    if difference is not None and difference > _TOTAL_TOLERANCE:
        items_total = sum((i.total_price for i in extracted.items if i.total_price is not None), Decimal("0"))
        reasons.append(
            {"code": "items_total_mismatch", "items_total": str(items_total), "total": str(extracted.total_amount)}
        )
    if not items_count_matches(extracted):
        reasons.append(
            {
                "code": "item_count_mismatch",
                "read": str(len(extracted.items)),
                "printed": str(extracted.metadata.get("items_count_printed")),
            }
        )
    return reasons


def consistency_issues(extracted: ExtractedDocument) -> list[str]:
    """Signs of a misreading that another model may fix (quality gate)."""
    issues: list[str] = []
    difference = items_total_difference(extracted)
    if difference is not None and difference > _TOTAL_TOLERANCE:
        issues.append(f"items total differs from document total by {difference}")
    if not items_count_matches(extracted):
        issues.append(
            f"read {len(extracted.items)} items but document lists {extracted.metadata.get('items_count_printed')}"
        )
    return issues


def validate_extraction(extracted: ExtractedDocument, today: date | None = None) -> ValidationResult:
    today = today or date.today()
    warnings: list[str] = []

    priced_items = [item.total_price for item in extracted.items if item.total_price is not None]
    items_total = sum(priced_items, Decimal("0")) if priced_items else None

    if extracted.total_amount is not None:
        amount = extracted.total_amount
    elif items_total is not None:
        amount = items_total
        warnings.append("Total amount not found; computed from item totals")
    else:
        raise PermanentProcessingError("No monetary value found in document")

    difference = items_total_difference(extracted)
    if difference is not None and difference > _TOTAL_TOLERANCE:
        warnings.append(f"Items total ({items_total}) differs from document total ({extracted.total_amount}) by {difference}")

    if not items_count_matches(extracted):
        printed = extracted.metadata.get("items_count_printed")
        warnings.append(f"Read {len(extracted.items)} items but document lists {printed}")

    if extracted.issued_at and extracted.issued_at > today + timedelta(days=1):
        warnings.append(f"Issue date {extracted.issued_at} is in the future")

    if extracted.issued_at and extracted.due_date and extracted.due_date < extracted.issued_at:
        warnings.append(f"Due date {extracted.due_date} is before issue date {extracted.issued_at}")

    if extracted.issued_at is None and extracted.due_date is None:
        warnings.append("No issue or due date found; using upload date")

    return ValidationResult(amount=amount, warnings=warnings)
