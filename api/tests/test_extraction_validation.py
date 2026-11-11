from datetime import date
from decimal import Decimal

import pytest

from schemas.extraction import DocumentType, ExtractedDocument, ExtractedItem
from services.document_processor import PermanentProcessingError
from services.extraction.validation import validate_extraction

TODAY = date(2024, 6, 1)


def doc(**fields) -> ExtractedDocument:
    return ExtractedDocument(document_type=DocumentType.OTHER, issued_at=date(2024, 5, 1), **fields)


def item(total: str) -> ExtractedItem:
    return ExtractedItem(description="x", total_price=Decimal(total))


def test_consistent_document_has_no_warnings():
    result = validate_extraction(doc(total_amount=Decimal("10.00"), items=[item("4.00"), item("6.00")]), TODAY)

    assert (result.amount, result.warnings) == (Decimal("10.00"), [])


def test_small_rounding_difference_is_tolerated():
    result = validate_extraction(doc(total_amount=Decimal("10.00"), items=[item("10.03")]), TODAY)

    assert result.warnings == []


def test_items_total_mismatch_is_a_warning():
    result = validate_extraction(doc(total_amount=Decimal("10.00"), items=[item("8.00")]), TODAY)

    assert result.amount == Decimal("10.00")
    assert "differs" in result.warnings[0]


def test_missing_total_is_computed_from_items():
    result = validate_extraction(doc(items=[item("1.50"), item("2.25")]), TODAY)

    assert result.amount == Decimal("3.75")
    assert "computed from item totals" in result.warnings[0]


def test_no_monetary_value_is_fatal():
    with pytest.raises(PermanentProcessingError, match="No monetary value"):
        validate_extraction(doc(items=[ExtractedItem(description="sem preço")]), TODAY)


def test_date_warnings():
    future = ExtractedDocument(
        document_type=DocumentType.ENERGY_BILL,
        total_amount=Decimal("100"),
        issued_at=date(2024, 7, 1),
        due_date=date(2024, 6, 20),
    )

    warnings = validate_extraction(future, TODAY).warnings

    assert any("in the future" in w for w in warnings)
    assert any("before issue date" in w for w in warnings)


def test_missing_dates_warning():
    undated = ExtractedDocument(document_type=DocumentType.OTHER, total_amount=Decimal("5"))

    assert any("upload date" in w for w in validate_extraction(undated, TODAY).warnings)


def test_schema_rejects_negative_money():
    with pytest.raises(ValueError):
        ExtractedItem(description="x", total_price=Decimal("-1.00"))



def test_warns_when_items_read_differ_from_printed_count():
    document = ExtractedDocument(
        document_type=DocumentType.NFCE,
        total_amount=Decimal("10.00"),
        items=[ExtractedItem(description="A", total_price=Decimal("10.00"))],
        metadata={"items_count_printed": "3"},
    )

    assert "Read 1 items but document lists 3" in validate_extraction(document).warnings


def sendas_like(**metadata) -> ExtractedDocument:
    """Real receipt (Assaí/Sendas): 13 lines, 'Qtd. total de itens 15' counts units,
    VALOR TOTAL 115,93 - DESCONTO 3,78 = A PAGAR 112,15."""
    lines = [("LV RP URCA 3L", "2", "43.58"), ("LTE INT ITALAC", "1", "6.25")]
    lines += [(f"CSCOX {i}", "0.9", price) for i, price in enumerate(["7.35", "9.45", "8.19", "7.69", "8.04", "10.05"])]
    lines += [("IOG FLAMB", "2", "8.78"), ("PIMENTAO", "0.194", "1.53"), ("CEBOLA", "0.366", "3.29"),
              ("TOMATE", "0.132", "1.43"), ("SAC BIO", "1", "0.30")]
    return ExtractedDocument(
        document_type=DocumentType.NFCE,
        issued_at=date(2026, 11, 8),
        total_amount=Decimal("112.15"),
        items=[ExtractedItem(description=d, quantity=Decimal(q), total_price=Decimal(p)) for d, q, p in lines],
        metadata={"items_count_printed": "15", **metadata},
    )


def test_discount_and_unit_count_are_consistent():
    from services.extraction.validation import consistency_issues

    document = sendas_like(discount="3.78")

    assert consistency_issues(document) == []
    assert validate_extraction(document).warnings == []


def test_without_discount_the_difference_is_flagged():
    from services.extraction.validation import consistency_issues

    issues = consistency_issues(sendas_like())

    assert issues == ["items total differs from document total by 3.78"]


def test_printed_count_matching_neither_lines_nor_units_is_flagged():
    from services.extraction.validation import consistency_issues

    issues = consistency_issues(sendas_like(discount="3.78", items_count_printed="20"))

    assert issues == ["read 13 items but document lists 20"]


def test_review_reasons_are_structured_codes():
    from services.extraction.validation import review_reasons

    reasons = review_reasons(sendas_like(items_count_printed="20"))

    assert reasons == [
        {"code": "items_total_mismatch", "items_total": "115.93", "total": "112.15"},
        {"code": "item_count_mismatch", "read": "13", "printed": "20"},
    ]
    assert review_reasons(sendas_like(discount="3.78")) == []


def test_utility_bill_with_partial_items_is_not_held_for_review():
    """Real water bill: only the water consumption tiers (72.13) were read as items; the total
    (131.42, with sewage) is what counts. The bill must not disappear from the totals and the bills to pay."""
    from services.extraction.validation import review_reasons

    bill = ExtractedDocument(
        document_type=DocumentType.WATER_BILL,
        issued_at=date(2026, 11, 9),
        total_amount=Decimal("131.42"),
        items=[item("46.03"), item("26.10")],
    )

    assert review_reasons(bill) == []
