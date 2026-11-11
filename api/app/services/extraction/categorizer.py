"""Automatic category of the entry (the user does not register anything).

Order: unambiguous document type (energy bill → Contas da casa) →
LLM suggestion → keywords in the establishment/description/items → Outros.
Deterministic and cheap: no extra model calls.
"""

import re
import unicodedata

from schemas.extraction import DocumentType, ExtractedDocument, SpendingCategory

_BY_DOCUMENT_TYPE = {
    DocumentType.ENERGY_BILL: SpendingCategory.HOME_BILLS,
    DocumentType.WATER_BILL: SpendingCategory.HOME_BILLS,
    DocumentType.TELECOM_BILL: SpendingCategory.HOME_BILLS,
    DocumentType.PHARMACY_RECEIPT: SpendingCategory.HEALTH,
    DocumentType.GROCERY_RECEIPT: SpendingCategory.GROCERIES,
    DocumentType.NFSE: SpendingCategory.SERVICES,
}

# Order matters: the first group that matches wins.
_KEYWORDS: list[tuple[SpendingCategory, tuple[str, ...]]] = [
    (SpendingCategory.HEALTH, ("farmacia", "drogaria", "droga", "clinica", "hospital", "laboratorio", "odonto", "medic")),
    (SpendingCategory.TRANSPORT, ("posto", "combustive", "gasolina", "etanol", "diesel", "uber", "99app", "estaciona", "pedagio", "auto posto")),
    (SpendingCategory.EATING_OUT, ("restaurante", "lanchonete", "pizzaria", "hamburg", "ifood", "bar ", "churrascaria", "sushi", "cafeteria")),
    (SpendingCategory.HOME_BILLS, ("energia", "eletric", "saneamento", "agua e esgoto", "telecom", "internet", "telefon", "gas ", "condominio", "aluguel")),
    (SpendingCategory.GROCERIES, ("supermercado", "mercado", "atacad", "hortifruti", "acougue", "padaria", "panificadora", "sacolao", "mercearia")),
    (SpendingCategory.SERVICES, ("assinatura", "netflix", "spotify", "escola", "curso", "oficina", "manutencao", "lavanderia")),
    (SpendingCategory.SHOPPING, ("magazine", "loja", "calcados", "moda", "eletro", "shopping")),
]


def _normalize(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", plain.lower()) + " "


def categorize(document: ExtractedDocument) -> SpendingCategory:
    if category := _BY_DOCUMENT_TYPE.get(document.document_type):
        return category
    if document.category and document.category is not SpendingCategory.OTHER:
        return document.category

    haystack = _normalize(" ".join(filter(None, [document.establishment, document.description])))
    for category, words in _KEYWORDS:
        if any(word in haystack for word in words):
            return category

    # Receipt with items and no hint in the name: almost always a grocery purchase.
    if document.document_type in (DocumentType.NFCE, DocumentType.NFE) and len(document.items) >= 3:
        return SpendingCategory.GROCERIES
    return document.category or SpendingCategory.OTHER
