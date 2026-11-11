"""FIXED set of documents for the assistant evaluation (fictitious, shaped like real receipts).

"Today" in the evaluation is EVAL_TODAY. Each document goes through the worker's real processor
(category, store name by CNPJ, items); only the AI reading is replaced by this one.
The totals below are the basis of the expected answers in cases.py: when changing a document,
recompute the cases that depend on it.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from schemas.extraction import DocumentType, ExtractedDocument, ExtractedItem, PaymentStatus, SpendingCategory

EVAL_TODAY = date(2026, 11, 9)

SENDAS = "06057223046163"
RCARVALHO = "32267292003135"
GRAO = "11444777000161"
FARMACIA = "45543915000181"
AGUAS = "06274757000150"
EQUATORIAL = "06840748000189"


@dataclass(frozen=True)
class EvalDocument:
    filename: str
    uploaded_at: datetime
    extracted: ExtractedDocument


def item(description: str, quantity: str, unit: str, unit_price: str, total: str) -> ExtractedItem:
    return ExtractedItem(
        description=description,
        quantity=Decimal(quantity),
        unit=unit,
        unit_price=Decimal(unit_price),
        total_price=Decimal(total),
    )


def receipt(day: date, establishment: str, tax_id: str, total: str, items: list[ExtractedItem], **metadata) -> ExtractedDocument:
    return ExtractedDocument(
        document_type=DocumentType.NFCE,
        description=f"Compra em {establishment}",
        establishment=establishment,
        establishment_tax_id=tax_id,
        issued_at=day,
        payment_status=PaymentStatus.PAID,
        total_amount=Decimal(total),
        items=items,
        metadata={"items_count_printed": str(len(items)), **metadata},
    )


def _uploaded(day: date, hour: int = 20) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0)


DOCUMENTS: list[EvalDocument] = [
    # --- Outubro -------------------------------------------------------------------
    EvalDocument(
        "rcarvalho-2026-10-30.jpg",
        _uploaded(date(2026, 11, 7), 10),
        receipt(date(2026, 10, 30), "RCARVALHO S DUMONT", RCARVALHO, "48.50", [
            item("CARNE BOU 2 PED COSSO", "1.956", "KG", "23.90", "46.75"),
            item("ALHO A GRANEL", "0.044", "KG", "39.90", "1.75"),
        ], legal_name="KRC COMERCIO DE ALIMENTOS LTDA"),
    ),
    EvalDocument(
        "rcarvalho-2026-10-31.jpg",
        _uploaded(date(2026, 11, 7), 11),
        receipt(date(2026, 10, 31), "RCARVALHO S DUMONT", RCARVALHO, "21.23", [
            item("LEITE INTEGRAL ITALAC 1L", "2", "UN", "5.49", "10.98"),
            item("CAFE SANTA CLARA 250G", "1", "UN", "10.25", "10.25"),
        ], legal_name="KRC COMERCIO DE ALIMENTOS LTDA"),
    ),
    EvalDocument(
        "aguas-de-teresina-2026-10.jpg",
        _uploaded(date(2026, 11, 7), 12),
        ExtractedDocument(
            document_type=DocumentType.WATER_BILL,
            description="Conta de água 10/2026",
            establishment="Águas de Teresina",
            establishment_tax_id=AGUAS,
            issued_at=date(2026, 10, 28),
            due_date=date(2026, 11, 12),
            payment_status=PaymentStatus.PENDING,
            total_amount=Decimal("131.42"),
            metadata={"Mês de referência": "10/2026", "Consumo": "18 m³"},
        ),
    ),
    EvalDocument(
        "equatorial-2026-10.jpg",
        _uploaded(date(2026, 11, 7), 13),
        ExtractedDocument(
            document_type=DocumentType.ENERGY_BILL,
            description="Conta de energia 10/2026",
            establishment="EQUATORIAL PIAUI",
            establishment_tax_id=EQUATORIAL,
            issued_at=date(2026, 10, 15),
            due_date=date(2026, 11, 5),
            payment_status=PaymentStatus.PAID,
            total_amount=Decimal("612.40"),
            items=[
                item("Consumo (kWh)", "470", "kWh", "1.11", "521.70"),
                item("Cip-Ilum Pub Pref Munic", "1", "UN", "58.50", "58.50"),
                item("Adicional Bandeira", "1", "UN", "32.20", "32.20"),
            ],
            metadata={"Mês de referência": "10/2026", "legal_name": "EQUATORIAL PIAUI DISTRIBUIDORA DE ENERGIA S.A"},
        ),
    ),
    # --- Novembro --------------------------------------------------------------------
    EvalDocument(
        "rcarvalho-2026-11-01.jpg",
        _uploaded(date(2026, 11, 7), 14),
        receipt(date(2026, 11, 1), "RCARVALHO S DUMONT", RCARVALHO, "123.83", [
            item("CARNE SUINA CTOU RESF DIV", "2.422", "KG", "21.90", "53.04"),
            item("OVOS BRANCOS 20UN", "1", "UN", "18.90", "18.90"),
            item("TOMATE KG", "0.500", "KG", "12.90", "6.45"),
            item("ARROZ TIPO 1 5KG", "1", "UN", "27.90", "27.90"),
            item("FEIJAO CARIOCA 1KG", "1", "UN", "8.49", "8.49"),
            item("DETERGENTE YPE 500ML", "2", "UN", "2.79", "5.58"),
            item("BANANA PRATA KG", "0.500", "KG", "6.94", "3.47"),
        ], legal_name="KRC COMERCIO DE ALIMENTOS LTDA"),
    ),
    EvalDocument(
        "rcarvalho-2026-11-06.jpg",
        _uploaded(date(2026, 11, 7), 15),
        receipt(date(2026, 11, 6), "RCARVALHO S DUMONT", RCARVALHO, "51.66", [
            item("SABTE LIQ PALM 200ML KI", "1", "UN", "15.90", "15.90"),
            item("TOMATE SALADA KG", "0.600", "KG", "11.90", "7.14"),
            item("CEBOLA KG", "0.400", "KG", "9.00", "3.60"),
            item("BATATA INGLESA KG", "1.000", "KG", "5.99", "5.99"),
            item("OVOS BRANCOS 12UN", "1", "UN", "9.90", "9.90"),
            item("LEITE ITALAC 1L", "1", "UN", "5.69", "5.69"),
            item("BANANA PRATA KG", "0.500", "KG", "6.88", "3.44"),
        ], legal_name="KRC COMERCIO DE ALIMENTOS LTDA"),
    ),
    EvalDocument(
        "mercado-do-grao-2026-11-06.jpg",
        _uploaded(date(2026, 11, 8), 18),
        receipt(date(2026, 11, 6), "MERCADO DO GRAO", GRAO, "30.68", [
            item("CAST CAJU CRUA KG", "0.204", "KG", "135.98", "27.74"),
            item("UVA PASSA KG", "0.060", "KG", "49.00", "2.94"),
        ]),
    ),
    EvalDocument(
        "sendas-2026-11-08.jpg",
        _uploaded(date(2026, 11, 8), 19),
        receipt(date(2026, 11, 8), "SENDAS DISTRIBUIDORA S/A", SENDAS, "112.15", [
            item("LV RP URCA 3L AZUL", "2", "UN", "21.79", "43.58"),
            item("LTE INT ITALAC 1L CT", "1", "UN", "6.25", "6.25"),
            item("CSCOX LAR C/DORSO", "0.898", "KG", "8.19", "7.35"),
            item("CSCOX LAR C/DORSO", "1.154", "KG", "8.19", "9.45"),
            item("CSCOX LAR C/DORSO", "1.000", "KG", "8.19", "8.19"),
            item("CSCOX LAR C/DORSO", "0.940", "KG", "8.19", "7.69"),
            item("CSCOX LAR C/DORSO", "0.982", "KG", "8.19", "8.04"),
            item("CSCOX LAR C/DORSO", "1.228", "KG", "8.19", "10.05"),
            item("IOG FLAMB 120G DESN", "2", "UN", "4.39", "8.78"),
            item("PIMENTAO VERDE KG", "0.194", "KG", "7.90", "1.53"),
            item("CEBOLA NAC KG", "0.366", "KG", "8.99", "3.29"),
            item("TOMATE SALADA KG", "0.132", "KG", "10.90", "1.43"),
            item("SAC 58X70 BIO VERDE", "1", "UN", "0.30", "0.30"),
        ], discount="3.78", items_count_printed="15"),
    ),
    EvalDocument(
        "rcarvalho-2026-11-08.jpg",
        _uploaded(date(2026, 11, 8), 19),
        receipt(date(2026, 11, 8), "RCARVALHO S DUMONT", RCARVALHO, "50.80", [
            item("ABOBORA COMUM", "0.320", "KG", "5.00", "1.60"),
            item("SAL REFINADO LEBRE 1000G", "1", "UN", "2.49", "2.49"),
            item("CHUCHU", "0.310", "KG", "5.00", "1.55"),
            item("CARNE BOV PED COXAO MOLE", "0.692", "KG", "49.90", "34.53"),
            item("REPOLHO VERDE", "0.266", "KG", "8.00", "2.13"),
            item("LING TOSC MISTA NOBRE", "0.294", "KG", "19.90", "5.85"),
            item("BATATA INGLESA ESPECIAL", "0.265", "KG", "10.00", "2.65"),
        ], legal_name="KRC COMERCIO DE ALIMENTOS LTDA"),
    ),
    # --- Other periods -----------------------------------------------------------------
    EvalDocument(
        "farmacia-globo-2026-09-04.jpg",
        _uploaded(date(2026, 11, 8), 19),
        # Pharmacy receipt with an NFC-e access key: the extractor classifies it as nfce (health category).
        receipt(date(2026, 9, 4), "EMPREENDIMENTOS FARMACEUTICOS GLOBO LTDA LJ 141", FARMACIA, "46.74", [
            item("PROT SOLAR NIVEA", "1", "UN", "46.74", "46.74"),
        ]).model_copy(update={"category": SpendingCategory.HEALTH}),
    ),
    EvalDocument(
        "energia-2025-11.jpg",
        _uploaded(date(2026, 11, 9), 21),
        # Photo without the utility's name: only the CNPJ (NF3e key). Same CNPJ as the other energy bill.
        ExtractedDocument(
            document_type=DocumentType.ENERGY_BILL,
            description="Conta de energia 11/2025",
            establishment_tax_id=EQUATORIAL,
            issued_at=date(2025, 11, 14),
            due_date=date(2025, 12, 5),
            payment_status=PaymentStatus.PENDING,
            total_amount=Decimal("729.08"),
            items=[
                item("Consumo (kWh)", "558", "kWh", "1.1149", "622.09"),
                item("Adicional Bandeira", "1", "UN", "33.49", "33.49"),
                item("Cip-Ilum Pub Pref Munic", "1", "UN", "58.50", "58.50"),
                item("Doacao LBV", "1", "UN", "15.00", "15.00"),
            ],
            metadata={"Mês de referência": "11/2025"},
        ),
    ),
]
