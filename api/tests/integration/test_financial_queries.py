from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from database.tenant_context import set_account_context
from models import Document, DocumentItem, FinancialEntry, PaymentStatus
from repositories.financial_queries import FinancialQueryRepository
from schemas.extraction import SPENDING_CATEGORY_NAMES, SpendingCategory

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("dev_environment")]


def add_purchase(session, account_id, *, doc_type, establishment, amount, day, items=(), filename=None, due=None):
    document = Document(
        account_id=account_id,
        original_filename=filename or f"{doc_type}-{day.isoformat()}.pdf",
        mime_type="application/pdf",
        blob_path=f"test/{uuid4()}",
        document_type=doc_type,
    )
    session.add(document)
    session.flush()
    entry = FinancialEntry(
        account_id=account_id,
        document_id=document.id,
        description=f"{establishment} {day.isoformat()}",
        establishment=establishment,
        amount=Decimal(amount),
        occurred_at=day,
        due_date=due,
        payment_status=PaymentStatus.PAID,
    )
    session.add(entry)
    session.flush()
    for description, unit_price, quantity in items:
        session.add(
            DocumentItem(
                account_id=account_id,
                document_id=document.id,
                financial_entry_id=entry.id,
                description=description,
                quantity=Decimal(quantity),
                unit="UN",
                unit_price=Decimal(unit_price),
                total_price=(Decimal(unit_price) * Decimal(quantity)).quantize(Decimal("0.01")),
            )
        )
    session.flush()
    return document


@pytest.fixture
def accounts(client, make_user, session):
    """Account A with purchases and bills; account B with data that must never show up for A."""
    owner_a, headers_a = make_user("fq-a@example.com")
    owner_b, headers_b = make_user("fq-b@example.com")
    a = UUID(client.post("/accounts/", json={"name": "A", "slug": "fq-a"}, headers=headers_a).json()["id"])
    b = UUID(client.post("/accounts/", json={"name": "B", "slug": "fq-b"}, headers=headers_b).json()["id"])

    set_account_context(session, b, UUID(owner_b))
    add_purchase(session, b, doc_type="nfce", establishment="Mercado B", amount="999.00", day=date(2026, 10, 5),
                 items=[("LEITE INTEGRAL 1L", "1.00", "1")])

    set_account_context(session, a, UUID(owner_a))
    add_purchase(session, a, doc_type="nfce", establishment="Mercado Exemplo", amount="63.43", day=date(2026, 10, 10),
                 items=[("LEITE INTEGRAL 1L", "4.89", "6"), ("PAO FRANCES KG", "15.99", "0.512")])
    add_purchase(session, a, doc_type="nfce", establishment="Atacadão Centro", amount="120.00", day=date(2026, 10, 20),
                 items=[("LEITE INTEGRAL 1L", "4.29", "12"), ("ARROZ 100% INTEGRAL", "9.90", "1"),
                        ("REFRIG COCA COLA 220ML", "2.89", "2")])
    add_purchase(session, a, doc_type="pharmacy_receipt", establishment="Farmácia Boa", amount="35.00", day=date(2026, 10, 15))
    add_purchase(session, a, doc_type="energy_bill", establishment="Cia Energia", amount="217.80", day=date(2026, 9, 15),
                 due=date(2026, 10, 5))
    add_purchase(session, a, doc_type="energy_bill", establishment="Cia Energia", amount="198.10", day=date(2026, 10, 15),
                 due=date(2026, 11, 5))
    return {"a": a, "b": b}


@pytest.fixture
def queries(session):
    return FinancialQueryRepository(session)


class TestSpendingSummary:
    def test_totals_by_period_and_type(self, queries, accounts):
        summary = queries.spending_summary(
            accounts["a"], start=date(2026, 10, 1), end=date(2026, 10, 31), document_types=["nfce", "grocery_receipt"]
        )

        assert (summary.total, summary.count) == (Decimal("183.43"), 2)
        assert [e.establishment for e in summary.by_establishment] == ["Atacadão Centro", "Mercado Exemplo"]
        assert summary.largest[0].amount == Decimal("120.00")

    def test_pharmacy_includes_health_receipts_of_other_types(self, queries, accounts, session):
        # A pharmacy receipt usually comes as NFC-e: the Health category completes the filter.
        from database.tenant_context import set_account_context
        from models import Category

        owner = session.execute(text("SELECT user_id FROM account_members WHERE account_id = :a"), {"a": accounts["a"]}).scalar()
        set_account_context(session, accounts["a"], owner)
        health = Category(account_id=accounts["a"], name=SPENDING_CATEGORY_NAMES[SpendingCategory.HEALTH])
        session.add(health)
        session.flush()
        document = add_purchase(session, accounts["a"], doc_type="nfce", establishment="Drogaria Centro", amount="46.74",
                                day=date(2026, 10, 18))
        entry = session.query(FinancialEntry).filter_by(document_id=document.id).one()
        entry.category_id = health.id
        session.flush()

        summary = queries.spending_summary(accounts["a"], start=date(2026, 10, 1), end=date(2026, 10, 31),
                                           document_types=["pharmacy_receipt"])

        assert (summary.total, summary.count) == (Decimal("81.74"), 2)  # pharmacy receipt + the drugstore's NFC-e

    def test_establishment_filter_ignores_accents_and_case(self, queries, accounts):
        summary = queries.spending_summary(accounts["a"], establishment="atacadao")

        assert (summary.total, summary.count) == (Decimal("120.00"), 1)

    def test_other_account_data_never_counted(self, queries, accounts):
        everything = queries.spending_summary(accounts["a"])

        assert everything.total == Decimal("634.33")  # without account B's R$ 999.00
        assert "Mercado B" not in [e.establishment for e in everything.by_establishment]

    def test_empty_period(self, queries, accounts):
        summary = queries.spending_summary(accounts["a"], start=date(2020, 1, 1), end=date(2020, 1, 31))

        assert (summary.total, summary.count, summary.largest) == (Decimal("0"), 0, [])


class TestItems:
    def test_cheapest_first(self, queries, accounts):
        rows = queries.items_matching(accounts["a"], "leite")

        assert [(r.establishment, r.unit_price) for r in rows] == [
            ("Atacadão Centro", Decimal("4.2900")),
            ("Mercado Exemplo", Decimal("4.8900")),
        ]

    def test_accent_insensitive_product(self, queries, accounts):
        assert [r.description for r in queries.items_matching(accounts["a"], "pão")] == ["PAO FRANCES KG"]

    def test_latest_first(self, queries, accounts):
        rows = queries.items_matching(accounts["a"], "leite", order="latest")

        assert [r.occurred_at for r in rows] == [date(2026, 10, 20), date(2026, 10, 10)]

    def test_like_wildcards_are_escaped(self, queries, accounts):
        assert [r.description for r in queries.items_matching(accounts["a"], "100%")] == ["ARROZ 100% INTEGRAL"]
        # "%" is literal, not a wildcard: only the item containing "%" shows up (not "every item").
        assert [r.description for r in queries.items_matching(accounts["a"], "%")] == ["ARROZ 100% INTEGRAL"]
        assert queries.items_matching(accounts["a"], "_") == []

    def test_abbreviated_items_match_long_product_names(self, queries, accounts):
        # The receipt prints "REFRIG ..."; the user asks for "refrigerante" (soda).
        assert [r.description for r in queries.items_matching(accounts["a"], "refrigerante")] == ["REFRIG COCA COLA 220ML"]
        assert queries.product_totals(accounts["a"], "refrigerantes").total_spent == Decimal("5.78")

    def test_short_terms_do_not_match_by_prefix(self, queries, accounts):
        # 6 letters (< 7): no prefix search; "refrim" is not contained in "REFRIG".
        assert queries.items_matching(accounts["a"], "refrim") == []

    def test_product_totals_sum_all_purchases(self, queries, accounts):
        totals = queries.product_totals(accounts["a"], "leite")

        # 6 × 4.89 + 12 × 4.29 (account B's R$ 1.00 purchase never counts).
        assert (totals.purchases, totals.total_spent) == (2, Decimal("80.82"))
        assert totals.quantity_by_unit == {"UN": Decimal("18.000")}

    def test_product_totals_without_purchases(self, queries, accounts):
        totals = queries.product_totals(accounts["a"], "caviar")

        assert (totals.purchases, totals.total_spent, totals.quantity_by_unit) == (0, Decimal("0"), {})

    def test_top_items_by_line_total(self, queries, accounts):
        rows = queries.top_items(accounts["a"], start=date(2026, 10, 1), end=date(2026, 10, 31), limit=3)

        # 12 × 4.29 > 6 × 4.89 > rice; account B's R$ 1.00 milk never counts.
        assert [(r.description, r.total_price) for r in rows] == [
            ("LEITE INTEGRAL 1L", Decimal("51.48")),
            ("LEITE INTEGRAL 1L", Decimal("29.34")),
            ("ARROZ 100% INTEGRAL", Decimal("9.90")),
        ]

    def test_top_items_filtered_by_establishment(self, queries, accounts):
        rows = queries.top_items(accounts["a"], establishment="mercado exemplo")

        assert [r.description for r in rows] == ["LEITE INTEGRAL 1L", "PAO FRANCES KG"]

    def test_top_products_sum_the_same_product(self, queries, accounts):
        products = queries.top_products(accounts["a"], limit=2)

        # 51.48 + 29.34 of milk summed; account B's milk never counts.
        assert [(p.description, p.purchases, p.total_spent) for p in products] == [
            ("LEITE INTEGRAL 1L", 2, Decimal("80.82")),
            ("ARROZ 100% INTEGRAL", 1, Decimal("9.90")),
        ]
        assert sorted(name for _, name in products[0].documents) == ["nfce-2026-10-10.pdf", "nfce-2026-10-20.pdf"]

    def test_items_from_other_account_never_returned(self, queries, accounts):
        assert all(r.establishment != "Mercado B" for r in queries.items_matching(accounts["a"], "leite"))


class TestBillsAndDocuments:
    def test_bills_by_month(self, queries, accounts):
        rows = queries.bills_by_month(accounts["a"], "energy_bill", since=date(2026, 6, 1))

        assert [(r.occurred_at.month, r.amount) for r in rows] == [(9, Decimal("217.80")), (10, Decimal("198.10"))]
        assert rows[-1].due_date == date(2026, 11, 5)

    def test_bills_full_history_and_upper_bound(self, queries, accounts):
        assert len(queries.bills_by_month(accounts["a"], "energy_bill")) == 2
        rows = queries.bills_by_month(accounts["a"], "energy_bill", until=date(2026, 9, 30))
        assert [r.amount for r in rows] == [Decimal("217.80")]

    def test_list_documents_by_period_and_type(self, queries, accounts):
        rows = queries.list_documents(
            accounts["a"], start=date(2026, 10, 1), end=date(2026, 10, 31), document_types=["pharmacy_receipt"]
        )

        assert [(r.document_type, r.amount) for r in rows] == [("pharmacy_receipt", Decimal("35.00"))]



def test_known_establishment_name_for_tax_id(session, accounts):
    from repositories.financial import FinancialRepository

    a = accounts["a"]
    session.add(FinancialEntry(
        account_id=a, description="compra", establishment="SUPERMERCADO EXEMPLO", amount=Decimal("10"),
        occurred_at=date(2026, 10, 1), metadata_={"establishment_tax_id": "32267292003135"},
    ))
    session.flush()
    repository = FinancialRepository(session)

    assert repository.establishment_name_for_tax_id(a, "32267292003135") == "SUPERMERCADO EXEMPLO"
    assert repository.establishment_name_for_tax_id(a, "00000000000000") is None
    # Isolated per account.
    assert repository.establishment_name_for_tax_id(accounts["b"], "32267292003135") is None


def test_rename_establishment_unifies_same_tax_id(session, accounts):
    from repositories.financial import FinancialRepository

    a = accounts["a"]
    for name, tax_id in (("COMERCIAL EXEMPLO LTDA", "32267292003135"), ("Comercial Exemplo", "32267292003135"), ("Outra Loja", "11111111000111")):
        session.add(FinancialEntry(
            account_id=a, description="compra", establishment=name, amount=Decimal("1"),
            occurred_at=date(2026, 10, 1), metadata_={"establishment_tax_id": tax_id},
        ))
    session.flush()

    FinancialRepository(session).rename_establishment(a, "32267292003135", "SUPERMERCADO EXEMPLO")
    session.expire_all()

    rows = session.execute(
        select(FinancialEntry.establishment, FinancialEntry.metadata_["establishment_tax_id"].astext)
        .where(FinancialEntry.account_id == a, FinancialEntry.description == "compra")
    ).all()
    assert sorted(rows) == [("Outra Loja", "11111111000111"), ("SUPERMERCADO EXEMPLO", "32267292003135"), ("SUPERMERCADO EXEMPLO", "32267292003135")]
