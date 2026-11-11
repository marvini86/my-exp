"""Frontend read routes: entries, dashboard and prices."""

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from database.tenant_context import set_account_context
from models import FinancialEntry, PaymentStatus
from services.financial import default_summary_start
from test_financial_queries import add_purchase

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("dev_environment")]

TODAY = date.today()


@pytest.fixture
def family(client, make_user, session):
    owner_id, owner = make_user("fin-owner@example.com")
    _, member = make_user("fin-member@example.com")
    other_id, other = make_user("fin-other@example.com")
    account_id = client.post("/accounts/", json={"name": "Família", "slug": "fin"}, headers=owner).json()["id"]
    client.post(f"/accounts/{account_id}/members/", json={"email": "fin-member@example.com"}, headers=owner)
    other_account = client.post("/accounts/", json={"name": "Outra", "slug": "fin-other"}, headers=other).json()["id"]
    mercado = client.post(f"/accounts/{account_id}/categories/", json={"name": "Mercado"}, headers=owner).json()["id"]

    # Neighbor account: must never show up.
    set_account_context(session, UUID(other_account), UUID(other_id))
    add_purchase(session, UUID(other_account), doc_type="nfce", establishment="Mercado Vizinho", amount="999.00",
                 day=date(2026, 10, 5), items=[("LEITE INTEGRAL 1L", "1.00", "1")])

    a = UUID(account_id)
    set_account_context(session, a, UUID(owner_id))
    add_purchase(session, a, doc_type="nfce", establishment="Mercado Exemplo", amount="63.43", day=date(2026, 10, 10),
                 items=[("LEITE INTEGRAL 1L", "4.89", "6"), ("PAO FRANCES KG", "15.99", "0.512")], filename="cupom.xml")
    add_purchase(session, a, doc_type="nfce", establishment="Atacadão Centro", amount="120.00", day=date(2026, 9, 20),
                 items=[("LEITE INTEGRAL 1L", "4.29", "12")])
    add_purchase(session, a, doc_type="pharmacy_receipt", establishment="Farmácia Boa", amount="35.00",
                 day=date(2026, 10, 15))
    bill = add_purchase(session, a, doc_type="energy_bill", establishment="Cia Energia", amount="198.10",
                        day=date(2026, 8, 15), due=TODAY + timedelta(days=5))
    paid_bill = add_purchase(session, a, doc_type="water_bill", establishment="Cia Água", amount="80.00",
                             day=date(2026, 8, 12), due=TODAY + timedelta(days=3))
    for document, status in ((bill, PaymentStatus.PENDING), (paid_bill, PaymentStatus.PAID)):
        entry = session.query(FinancialEntry).filter_by(document_id=document.id).one()
        entry.payment_status = status
    session.flush()

    return {"id": account_id, "owner": owner, "member": member, "other": other, "mercado": mercado,
            "base": f"/accounts/{account_id}"}


def entries(client, family, who="owner", **params):
    response = client.get(f"{family['base']}/entries/", params=params, headers=family[who])
    assert response.status_code == 200, response.text
    return response.json()


class TestEntries:
    def test_list_is_newest_first_and_paginated(self, client, family):
        page = entries(client, family, limit=2)

        assert page["total"] == 5
        assert len(page["items"]) == 2
        # Sum of every page, not only the 2 rows returned.
        assert page["total_expenses"] == "496.53"
        assert entries(client, family, q="atacadao")["total_expenses"] == "120.00"
        dates = [e["occurred_at"] for e in entries(client, family)["items"]]
        assert dates == sorted(dates, reverse=True)

        rest = entries(client, family, limit=2, offset=4)
        assert len(rest["items"]) == 1

    def test_shape(self, client, family):
        cupom = next(e for e in entries(client, family)["items"] if e["document_filename"] == "cupom.xml")

        assert cupom["amount"] == "63.43"
        assert (cupom["document_type"], cupom["establishment"], cupom["category"]) == ("nfce", "Mercado Exemplo", None)

    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            ({"q": "atacadao"}, ["Atacadão Centro"]),
            ({"document_type": "pharmacy_receipt"}, ["Farmácia Boa"]),
            ({"start": "2026-10-01", "end": "2026-10-31"}, ["Farmácia Boa", "Mercado Exemplo"]),
            ({"payment_status": "pending"}, ["Cia Energia"]),
        ],
    )
    def test_filters(self, client, family, params, expected):
        assert sorted(e["establishment"] for e in entries(client, family, **params)["items"]) == sorted(expected)

    def test_other_account_never_listed(self, client, family):
        assert all(e["establishment"] != "Mercado Vizinho" for e in entries(client, family, limit=100)["items"])

    def test_member_categorizes_and_filters(self, client, family):
        entry = entries(client, family, q="atacadao")["items"][0]
        url = f"{family['base']}/entries/{entry['id']}"

        updated = client.patch(url, json={"category_id": family["mercado"]}, headers=family["member"])

        assert updated.status_code == 200
        assert updated.json()["category"] == {"id": family["mercado"], "name": "Mercado"}
        assert [e["id"] for e in entries(client, family, category_id=family["mercado"])["items"]] == [entry["id"]]
        assert entry["id"] not in [e["id"] for e in entries(client, family, uncategorized=True)["items"]]

        cleared = client.patch(url, json={"category_id": None}, headers=family["member"])
        assert cleared.json()["category"] is None

    def test_category_from_another_account_is_not_found(self, client, family):
        foreign = client.post("/accounts/", json={"name": "X", "slug": "fin-x"}, headers=family["other"]).json()["id"]
        foreign_category = client.post(
            f"/accounts/{foreign}/categories/", json={"name": "Alheia"}, headers=family["other"]
        ).json()["id"]
        entry = entries(client, family)["items"][0]

        response = client.patch(
            f"{family['base']}/entries/{entry['id']}", json={"category_id": foreign_category}, headers=family["owner"]
        )

        assert response.status_code == 404
        assert response.json() == {"detail": "Category not found"}

    def test_unknown_entry_and_outsider(self, client, family):
        assert client.get(f"{family['base']}/entries/{uuid4()}", headers=family["owner"]).status_code == 404
        assert client.get(f"{family['base']}/entries/", headers=family["other"]).status_code == 404

    def test_inverted_period_is_bad_request(self, client, family):
        response = client.get(
            f"{family['base']}/entries/", params={"start": "2026-10-31", "end": "2026-10-01"}, headers=family["owner"]
        )

        assert response.status_code == 400


class TestSummary:
    def test_totals_by_month_category_establishment_and_type(self, client, family):
        response = client.get(
            f"{family['base']}/reports/summary", params={"start": "2026-09-01", "end": "2026-10-31"}, headers=family["owner"]
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["total"], body["count"]) == ("218.43", 3)
        assert [(m["month"], m["total"]) for m in body["by_month"]] == [("2026-09", "120.00"), ("2026-10", "98.43")]
        assert body["by_category"] == [{"key": None, "label": "Sem categoria", "total": "218.43", "count": 3}]
        assert body["by_establishment"][0]["label"] == "Atacadão Centro"
        assert {g["key"]: g["label"] for g in body["by_document_type"]} == {
            "nfce": "Cupom fiscal (NFC-e)",
            "pharmacy_receipt": "Recibo de farmácia",
        }

    def test_top_products_sum_the_same_item(self, client, family):
        body = client.get(
            f"{family['base']}/reports/summary", params={"start": "2026-09-01", "end": "2026-10-31"}, headers=family["owner"]
        ).json()

        # 6 × 4.89 + 12 × 4.29; the neighbor account's milk never counts.
        assert [(p["label"], p["total"], p["count"]) for p in body["top_products"]] == [
            ("LEITE INTEGRAL 1L", "80.82", 2),
            ("PAO FRANCES KG", "8.19", 1),
        ]

    def test_open_bills_exclude_paid(self, client, family):
        body = client.get(f"{family['base']}/reports/summary", headers=family["member"]).json()

        assert [b["establishment"] for b in body["open_bills"]] == ["Cia Energia"]
        assert body["open_bills"][0]["due_date"] == (TODAY + timedelta(days=5)).isoformat()

    def test_default_period_is_six_months(self, client, family):
        period = client.get(f"{family['base']}/reports/summary", headers=family["owner"]).json()["period"]

        assert period == {"start": default_summary_start(TODAY).isoformat(), "end": TODAY.isoformat()}

    def test_period_limit(self, client, family):
        response = client.get(
            f"{family['base']}/reports/summary", params={"start": "2020-01-01", "end": "2026-01-01"},
            headers=family["owner"],
        )

        assert response.status_code == 400


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2026, 11, 6), date(2026, 6, 1)), (date(2026, 3, 31), date(2025, 10, 1)), (date(2026, 6, 1), date(2026, 1, 1))],
)
def test_default_summary_start(today, expected):
    assert default_summary_start(today) == expected


class TestItemPrices:
    def test_prices_and_stats(self, client, family):
        response = client.get(f"{family['base']}/items/", params={"q": "leite"}, headers=family["member"])

        assert response.status_code == 200, response.text
        body = response.json()
        assert [(i["establishment"], i["unit_price"]) for i in body["items"]] == [
            ("Atacadão Centro", "4.2900"),
            ("Mercado Exemplo", "4.8900"),
        ]
        assert body["stats"] == {
            "count": 2, "min_unit_price": "4.2900", "max_unit_price": "4.8900", "avg_unit_price": "4.5900",
        }

    def test_latest_order_and_accents(self, client, family):
        latest = client.get(f"{family['base']}/items/", params={"q": "leite", "order": "latest"}, headers=family["owner"])
        bread = client.get(f"{family['base']}/items/", params={"q": "pão"}, headers=family["owner"])

        assert [i["occurred_at"] for i in latest.json()["items"]] == ["2026-10-10", "2026-09-20"]
        assert [i["description"] for i in bread.json()["items"]] == ["PAO FRANCES KG"]

    def test_no_match(self, client, family):
        body = client.get(f"{family['base']}/items/", params={"q": "caviar"}, headers=family["owner"]).json()

        assert body["items"] == []
        assert body["stats"] == {"count": 0, "min_unit_price": None, "max_unit_price": None, "avg_unit_price": None}

    def test_query_is_required(self, client, family):
        assert client.get(f"{family['base']}/items/", headers=family["owner"]).status_code == 422


def test_decimal_amount_has_no_float_error(client, family):
    total = sum(Decimal(e["amount"]) for e in entries(client, family, limit=100)["items"])

    assert total == Decimal("496.53")
