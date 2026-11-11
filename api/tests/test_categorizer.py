from decimal import Decimal

import pytest

from schemas.extraction import DocumentType, ExtractedDocument, ExtractedItem, SpendingCategory
from services.extraction.categorizer import categorize
from services.extraction.llm import _category


def doc(document_type=DocumentType.OTHER, establishment=None, category=None, items=0, description=None):
    return ExtractedDocument(
        document_type=document_type,
        establishment=establishment,
        description=description,
        category=category,
        total_amount=Decimal("10.00"),
        items=[ExtractedItem(description=f"ITEM {i}") for i in range(items)],
    )


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (doc(DocumentType.ENERGY_BILL, category=SpendingCategory.SHOPPING), SpendingCategory.HOME_BILLS),
        (doc(DocumentType.PHARMACY_RECEIPT), SpendingCategory.HEALTH),
        (doc(category=SpendingCategory.TRANSPORT, establishment="Supermercado X"), SpendingCategory.TRANSPORT),
        (doc(DocumentType.NFCE, establishment="DROGARIA SÃO PAULO"), SpendingCategory.HEALTH),
        (doc(DocumentType.NFCE, establishment="Auto Posto Shell"), SpendingCategory.TRANSPORT),
        (doc(DocumentType.NFCE, establishment="Atacadão Centro"), SpendingCategory.GROCERIES),
        (doc(DocumentType.NFCE, establishment="Pizzaria Bella"), SpendingCategory.EATING_OUT),
        (doc(DocumentType.NFCE, establishment="Comercial Fulano LTDA", items=5), SpendingCategory.GROCERIES),
        (doc(DocumentType.BANK_SLIP, establishment="Fulano LTDA"), SpendingCategory.OTHER),
    ],
    ids=["tipo-vence-llm", "farmacia-tipo", "llm", "drogaria", "posto", "atacadao", "pizzaria", "cupom-com-itens", "sem-pista"],
)
def test_categorize(document, expected):
    assert categorize(document) is expected


def test_llm_other_falls_back_to_keywords():
    assert categorize(doc(establishment="Supermercado Bom", category=SpendingCategory.OTHER)) is SpendingCategory.GROCERIES


@pytest.mark.parametrize(("raw", "expected"), [("mercado", SpendingCategory.GROCERIES), (" Saude ", SpendingCategory.HEALTH), ("pets", None), (None, None)])
def test_llm_category_is_tolerant(raw, expected):
    assert _category(raw) is expected
