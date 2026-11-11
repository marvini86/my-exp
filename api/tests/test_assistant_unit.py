from datetime import date
from uuid import uuid4

import pytest

from schemas.extraction import DocumentType
from services.assistant.answer import build_answer_messages, resolve_citations
from services.assistant.planner import Intent, QueryPlan, bills_since, build_planner_messages, normalize_plan
from services.assistant.tools import Source, ToolOutput

TODAY = date(2026, 11, 6)


class TestPlanner:
    def test_prompt_contains_today_and_history(self):
        messages = build_planner_messages("e no mês anterior?", [("user", "quanto gastei em outubro?")], TODAY)

        assert "Hoje é 2026-11-06 (sexta-feira)" in messages[0].content
        assert "quanto gastei em outubro?" in messages[1].content
        assert "e no mês anterior?" in messages[1].content

    def test_future_dates_are_clamped_and_order_fixed(self):
        plan = QueryPlan(intent=Intent.SPENDING_SUMMARY, start_date=date(2026, 12, 31), end_date=date(2026, 10, 1))

        normalized = normalize_plan(plan, "q", TODAY)

        assert (normalized.start_date, normalized.end_date) == (date(2026, 10, 1), TODAY)

    @pytest.mark.parametrize("intent", [Intent.PRICE_HISTORY, Intent.LAST_PURCHASE])
    def test_product_intent_without_product_becomes_search(self, intent):
        normalized = normalize_plan(QueryPlan(intent=intent, product="  "), "onde comprei mais barato?", TODAY)

        assert normalized.intent is Intent.DOCUMENT_SEARCH
        assert normalized.search_query == "onde comprei mais barato?"

    def test_spending_on_a_product_becomes_price_history(self):
        # "how much did I spend on eggs?": sum of items, not the total of entries.
        plan = QueryPlan(intent=Intent.SPENDING_SUMMARY, product="ovos", document_types=[DocumentType.NFCE])

        assert normalize_plan(plan, "quanto gastei com ovos?", TODAY).intent is Intent.PRICE_HISTORY

    @pytest.mark.parametrize(
        ("question", "expected"),
        [
            ("quanto gastei no mercado este mês?", [DocumentType.NFCE, DocumentType.GROCERY_RECEIPT]),
            ("gastos na farmácia em outubro", [DocumentType.PHARMACY_RECEIPT]),
            ("quanto paguei de luz?", [DocumentType.ENERGY_BILL]),
            ("quanto gastei em outubro?", []),
        ],
    )
    def test_spending_type_comes_from_fixed_words(self, question, expected):
        plan = normalize_plan(QueryPlan(intent=Intent.SPENDING_SUMMARY), question, TODAY)

        assert plan.document_types == expected

    def test_types_chosen_by_the_model_are_kept(self):
        plan = QueryPlan(intent=Intent.SPENDING_SUMMARY, document_types=[DocumentType.WATER_BILL])

        assert normalize_plan(plan, "quanto gastei no mercado?", TODAY).document_types == [DocumentType.WATER_BILL]

    def test_bills_comparison_defaults_to_energy_and_full_history(self):
        normalized = normalize_plan(QueryPlan(intent=Intent.BILLS_COMPARISON), "última conta", TODAY)

        assert normalized.document_types == [DocumentType.ENERGY_BILL]
        assert (normalized.months, normalized.start_date, normalized.end_date) == (None, None, None)

    def test_bills_comparison_keeps_only_one_bill_type(self):
        plan = QueryPlan(intent=Intent.BILLS_COMPARISON, document_types=[DocumentType.NFCE, DocumentType.WATER_BILL])

        assert normalize_plan(plan, "q", TODAY).document_types == [DocumentType.WATER_BILL]

    def test_months_are_bounded_by_schema(self):
        with pytest.raises(ValueError):
            QueryPlan(intent=Intent.BILLS_COMPARISON, months=100)

    @pytest.mark.parametrize(("months", "expected"), [(1, date(2026, 11, 1)), (6, date(2026, 6, 1)), (12, date(2025, 12, 1))])
    def test_bills_since(self, months, expected):
        assert bills_since(months, TODAY) == expected


class TestAnswer:
    ACCOUNT = uuid4()
    SOURCES = [Source("D1", uuid4(), "cupom.xml"), Source("D2", uuid4(), "conta.pdf")]

    def test_valid_citations_kept_in_order_of_use(self):
        text, citations = resolve_citations("Luz custou R$ 217,80 [D2]; leite R$ 4,89 [D1] [D2].", self.SOURCES, self.ACCOUNT)

        assert text == "Luz custou R$ 217,80 [D2]; leite R$ 4,89 [D1] [D2]."
        assert [c["ref"] for c in citations] == ["D2", "D1"]
        assert citations[0]["download_path"] == f"/accounts/{self.ACCOUNT}/documents/{self.SOURCES[1].document_id}/download"

    def test_invented_citations_are_removed(self):
        text, citations = resolve_citations("Total R$ 10,00 [D9].", self.SOURCES, self.ACCOUNT)

        # [D9] does not exist: it disappears. With no valid citation, the real sources go at the end.
        assert text.startswith("Total R$ 10,00.\n\nFontes: ")
        assert "[D9]" not in text
        assert [c["ref"] for c in citations] == [s.ref for s in self.SOURCES]

    def test_answer_prompt_carries_data_and_sources(self):
        output = ToolOutput("price_history", {"has_results": True, "mais_barato": {"preco_unitario": "R$ 4,89"}}, self.SOURCES)

        messages = build_answer_messages("leite mais barato?", [], output)

        assert "NÃO refaça contas" in messages[0].content
        assert "R$ 4,89" in messages[1].content
        assert "[D1] cupom.xml" in messages[1].content


class TestFollowUpsAndFallbacks:
    def test_this_month_reaches_end_of_month(self):
        # A bill dated 11/09 must be included in "this month" when today is 11/06.
        plan = QueryPlan(intent=Intent.SPENDING_SUMMARY, start_date=date(2026, 11, 1), end_date=TODAY)

        assert normalize_plan(plan, "conta de água deste mês", TODAY).end_date == date(2026, 11, 30)

    def test_past_periods_are_untouched(self):
        plan = QueryPlan(intent=Intent.SPENDING_SUMMARY, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31))

        assert normalize_plan(plan, "outubro", TODAY).end_date == date(2026, 10, 31)

    @pytest.mark.parametrize("written", ["[ D1 ]", "【D1】", "［D1］", "[D1]", "[fonte D1]", "[Fonte: D1]", "[​D1]"])
    def test_citation_spellings_are_kept_and_normalized(self, written):
        sources = [Source("D1", uuid4(), "cupom.jpg")]

        text, citations = resolve_citations(f"Total R$ 10,00 {written}.", sources, uuid4())

        assert text == "Total R$ 10,00 [D1]."
        assert [c["ref"] for c in citations] == ["D1"]

    def test_product_without_items_falls_back_to_semantic_search(self):
        from types import SimpleNamespace

        from services.assistant.tools import AssistantTools

        class NoItems:
            def items_matching(self, *a, **k):
                return []

            def product_totals(self, *a, **k):
                return SimpleNamespace(purchases=0, total_spent=0, quantity_by_unit={})

            def pending_review_count(self, *a, **k):
                return 0

        class Search:
            def search(self, context, query, limit):
                self.query = query
                hit = SimpleNamespace(document_id=uuid4(), original_filename="cupom.jpg", content="DET LIQ PONTAL 500ML; AGUA SANITARIA")
                return SimpleNamespace(results=[hit])

        search = Search()
        plan = normalize_plan(QueryPlan(intent=Intent.PRICE_HISTORY, product="produto de limpeza"), "quais produtos de limpeza comprei?", TODAY)
        context = SimpleNamespace(account_id=uuid4())

        output = AssistantTools(NoItems(), search).run(plan, context, TODAY)

        assert output.tool == "document_search"
        assert search.query == "quais produtos de limpeza comprei?"
        assert "AGUA SANITARIA" in output.data["trechos"][0]["trecho"]
        assert "produto de limpeza" in output.data["observacao"]


def test_grocery_receipt_and_nfce_go_together():
    plan = QueryPlan(intent=Intent.LIST_DOCUMENTS, document_types=[DocumentType.GROCERY_RECEIPT])

    types = normalize_plan(plan, "a nota do tomate", TODAY).document_types

    assert set(types) == {DocumentType.GROCERY_RECEIPT, DocumentType.NFCE}


def test_invented_markers_are_removed():
    text, citations = resolve_citations("Não encontrei【DADOS】. Valor R$ 1,00【D1】.", [Source("D1", uuid4(), "c.jpg")], uuid4())

    assert text == "Não encontrei. Valor R$ 1,00[D1]."
    assert len(citations) == 1


def test_today_does_not_stretch_to_end_of_month():
    # "how much did I spend today" used to come out as 11/06..11/30
    plan = QueryPlan(intent=Intent.SPENDING_SUMMARY, start_date=TODAY, end_date=date(2026, 11, 30))

    normalized = normalize_plan(plan, "quanto gastei hoje no mercado?", TODAY)

    assert (normalized.start_date, normalized.end_date) == (TODAY, TODAY)


def test_top_items_is_a_valid_intent():
    plan = normalize_plan(QueryPlan(intent=Intent.TOP_ITEMS, start_date=TODAY, end_date=TODAY), "quais itens foram mais caros?", TODAY)

    assert plan.intent is Intent.TOP_ITEMS
    assert "top_items" in build_planner_messages("quais itens foram mais caros?", [], TODAY)[0].content


def test_bare_refs_in_table_cells_become_citations():
    doc_a, doc_b = uuid4(), uuid4()
    sources = [Source(ref="D1", document_id=doc_a, original_filename="a.jpg"), Source(ref="D2", document_id=doc_b, original_filename="b.jpg")]
    text = "| Produto | Fonte |\n|---|---|\n| CARNE | D1 |\n| SABAO | D1, D2 |\n| DVD | X |"

    cleaned, citations = resolve_citations(text, sources, uuid4())

    assert "| CARNE | [D1] |" in cleaned and "| SABAO | [D1], [D2] |" in cleaned
    assert [c["ref"] for c in citations] == ["D1", "D2"]


@pytest.mark.parametrize("question", ["quanto paguei de internet?", "minha conta de telefone subiu?"])
def test_phone_and_internet_are_telecom_bills(question):
    plan = normalize_plan(QueryPlan(intent=Intent.SPENDING_SUMMARY), question, TODAY)

    assert plan.document_types == [DocumentType.TELECOM_BILL]


def test_cellphone_as_a_product_is_not_a_bill():
    plan = normalize_plan(QueryPlan(intent=Intent.PRICE_HISTORY, product="carregador de celular"), "quanto paguei no carregador de celular?", TODAY)

    assert DocumentType.TELECOM_BILL not in plan.document_types


@pytest.mark.parametrize(
    ("status", "due", "expected"),
    [
        ("pending", date(2025, 12, 5), "vencido em 05/12/2025 (não há registro de pagamento)"),
        ("pending", date(2026, 11, 20), "a pagar"),
        ("paid", date(2025, 12, 5), "pago"),
        ("pending", None, "a pagar"),
    ],
)
def test_bill_status_flags_overdue(status, due, expected):
    from services.assistant.tools import _bill_status

    assert _bill_status(status, due, TODAY) == expected


# --- fixes found by the evaluation (evals/assistant) -----------------------------------------


@pytest.mark.parametrize("question", ["qual foi minha maior compra este mês?", "qual a nota mais cara de outubro?"])
def test_biggest_purchase_is_the_whole_receipt(question):
    plan = normalize_plan(QueryPlan(intent=Intent.TOP_ITEMS, start_date=date(2026, 11, 1), end_date=TODAY), question, TODAY)

    assert plan.intent is Intent.SPENDING_SUMMARY


def test_bills_to_pay_go_to_bills_comparison():
    plan = normalize_plan(QueryPlan(intent=Intent.LIST_DOCUMENTS), "tenho alguma conta de luz para pagar?", TODAY)

    assert (plan.intent, plan.document_types) == (Intent.BILLS_COMPARISON, [DocumentType.ENERGY_BILL])


def test_receipt_details_are_not_out_of_scope():
    plan = normalize_plan(QueryPlan(intent=Intent.OUT_OF_SCOPE), "qual o CNPJ do Sendas?", TODAY)

    assert plan.intent is Intent.DOCUMENT_SEARCH


def test_greeting_stays_out_of_scope():
    assert normalize_plan(QueryPlan(intent=Intent.OUT_OF_SCOPE), "bom dia!", TODAY).intent is Intent.OUT_OF_SCOPE


# --- fixes from production chats (11/2026) ---------------------------------------------------


ALL_BILLS = [DocumentType.ENERGY_BILL, DocumentType.WATER_BILL, DocumentType.TELECOM_BILL]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Quais Contas da casa já tenho q pagar esse mes?", ALL_BILLS),
        ("tenho contas para pagar?", ALL_BILLS),
        ("Contas de casa sao de agua e Luz, quais tenho q pagar?", [DocumentType.ENERGY_BILL, DocumentType.WATER_BILL]),
    ],
)
def test_bills_to_pay_cover_every_bill_type_asked(question, expected):
    # The model picked only electricity and the current month; a bill to pay may have been issued earlier.
    plan = QueryPlan(intent=Intent.BILLS_COMPARISON, document_types=[DocumentType.ENERGY_BILL],
                     start_date=date(2026, 11, 1), end_date=date(2026, 11, 30))

    normalized = normalize_plan(plan, question, TODAY)

    assert (normalized.intent, normalized.document_types) == (Intent.BILLS_COMPARISON, expected)
    assert (normalized.start_date, normalized.end_date) == (None, None)


def test_follow_up_naming_bill_types_keeps_them_all():
    plan = QueryPlan(intent=Intent.BILLS_COMPARISON, document_types=[DocumentType.WATER_BILL])

    normalized = normalize_plan(plan, "Contas de casa sao de agua e Luz", TODAY)

    assert normalized.document_types == [DocumentType.ENERGY_BILL, DocumentType.WATER_BILL]


def test_water_as_a_product_is_not_a_bill():
    plan = normalize_plan(QueryPlan(intent=Intent.PRICE_HISTORY, product="água mineral"), "quanto paguei na água mineral?", TODAY)

    assert plan.intent is Intent.PRICE_HISTORY and plan.document_types == []


def test_comparison_period_needs_both_periods():
    october = {"compare_start_date": date(2026, 10, 1), "compare_end_date": date(2026, 10, 31)}
    november = {"start_date": date(2026, 11, 1), "end_date": TODAY}

    kept = normalize_plan(QueryPlan(intent=Intent.PRICE_HISTORY, product="legumes", **november, **october), "q", TODAY)
    dropped = normalize_plan(QueryPlan(intent=Intent.PRICE_HISTORY, product="legumes", **october), "q", TODAY)

    assert (kept.compare_start_date, kept.compare_end_date) == (date(2026, 10, 1), date(2026, 10, 31))
    assert (dropped.compare_start_date, dropped.compare_end_date) == (None, None)


def test_planner_sees_the_previous_plan():
    # "legumes são tomate, cebola..." lost the "this month" of the previous question.
    previous = {"intent": "price_history", "product": "legumes", "start_date": "2026-11-01", "end_date": "2026-11-30",
                "establishment": None, "document_types": []}

    content = build_planner_messages("Legumes são tomates, cebolas e etc", [], TODAY, previous)[1].content

    assert '"product": "legumes"' in content and '"start_date": "2026-11-01"' in content
    assert "establishment" not in content  # campos vazios ficam de fora
    assert "PLANO ANTERIOR:\n(nenhum)" in build_planner_messages("oi", [], TODAY)[1].content


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("Total R$ 553,87 [spending_summary].", "Total R$ 553,87."),
        ("Nada encontrado. [fonte: dados da ferramenta bills_comparison]", "Nada encontrado."),
        ("Nada encontrado (dados da ferramenta bills_comparison).", "Nada encontrado."),
        ("Você gastou R$ 500,54【】.", "Você gastou R$ 500,54."),
    ],
)
def test_internal_names_never_reach_the_user(written, expected):
    text, _ = resolve_citations(written, [], uuid4())

    assert text == expected


def test_answer_prompt_does_not_carry_the_tool_name():
    output = ToolOutput("spending_summary", {"has_results": True}, [])

    assert "spending_summary" not in build_answer_messages("q", [], output)[1].content


class FakeLLM:
    def __init__(self, matches=None, error=None):
        self.matches, self.error, self.messages = matches, error, None

    def structured(self, messages, schema):
        self.messages = messages
        if self.error:
            raise self.error
        return schema(matches=self.matches), "fake"


class TestItemResolver:
    NAMES = ["CSCOX LAR C/DORSO", "LTE INT ITALAC 1L CT", "CR LEITE TP LEITBOM 200G", "TOMATE SALADA"]

    def test_returns_the_chosen_names(self):
        from services.assistant.items import ItemResolver

        llm = FakeLLM(matches=[2, 2, 9, 0])

        assert ItemResolver(llm).select("leite", "qt gastei de leite?", self.NAMES) == ["LTE INT ITALAC 1L CT"]
        assert "1. CSCOX LAR C/DORSO" in llm.messages[1].content  # numbers that do not exist are ignored

    def test_unavailable_ai_means_text_search(self):
        from services.assistant.items import ItemResolver
        from services.llm_router import LLMUnavailableError

        llm = FakeLLM(error=LLMUnavailableError("sem modelos", 60))

        assert ItemResolver(llm).select("frango", "e o frango?", self.NAMES) is None
        assert ItemResolver(llm).select("frango", "e o frango?", []) == []


class FakeQueries:
    """Only what the product tools use; records the arguments received."""

    def __init__(self, names=(), rows=()):
        self.names, self.rows, self.calls = list(names), list(rows), []

    def item_vocabulary(self, account_id, *, start=None, end=None, limit=400):
        self.calls.append(("vocabulary", start, end))
        return self.names, False

    def items_matching(self, account_id, product, **kwargs):
        self.calls.append(("items", kwargs.get("descriptions"), kwargs.get("start")))
        return [r for r in self.rows if kwargs.get("descriptions") is None or r.description in kwargs["descriptions"]]

    def product_totals(self, account_id, product, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(purchases=0, total_spent=0, quantity_by_unit={})

    def price_series(self, account_id, **kwargs):
        return self.rows

    def spending_summary(self, account_id, **kwargs):
        from decimal import Decimal
        from types import SimpleNamespace

        return SimpleNamespace(total=Decimal("422.45"), count=7)

    def pending_review_count(self, account_id):
        return 0


def item_row(description, day, unit_price, establishment="Loja"):
    from decimal import Decimal
    from types import SimpleNamespace

    return SimpleNamespace(
        document_id=uuid4(), original_filename=f"{day}.jpg", description=description, establishment=establishment,
        occurred_at=day, quantity=Decimal("1"), unit="UN", unit_price=Decimal(unit_price), total_price=Decimal(unit_price),
    )


def test_price_history_counts_what_the_resolver_chose():
    from types import SimpleNamespace

    from services.assistant.items import ItemResolver
    from services.assistant.tools import AssistantTools

    rows = [item_row("CSCOX LAR C/DORSO", date(2026, 11, 8), "8.19"), item_row("CARNE BOV PED", date(2026, 11, 8), "49.90")]
    queries = FakeQueries(names=["CSCOX LAR C/DORSO", "CARNE BOV PED"], rows=rows)
    plan = normalize_plan(QueryPlan(intent=Intent.PRICE_HISTORY, product="frango", start_date=date(2026, 11, 1),
                                    end_date=TODAY), "E o frango?", TODAY)

    output = AssistantTools(queries, search=None, resolver=ItemResolver(FakeLLM(matches=[1]))).run(
        plan, SimpleNamespace(account_id=uuid4()), TODAY
    )

    assert output.tool == "price_history"
    assert [c["produto"] for c in output.data["compras"]] == ["CSCOX LAR C/DORSO"]
    assert output.data["itens_considerados"] == ["CSCOX LAR C/DORSO"]
    assert ("vocabulary", date(2026, 11, 1), date(2026, 11, 30)) in queries.calls


def test_comparison_lists_both_periods():
    from types import SimpleNamespace

    from services.assistant.tools import AssistantTools

    queries = FakeQueries(rows=[item_row("TOMATE", date(2026, 11, 6), "12.90")])
    plan = normalize_plan(
        QueryPlan(intent=Intent.PRICE_HISTORY, product="tomate", start_date=date(2026, 11, 1), end_date=TODAY,
                  compare_start_date=date(2026, 10, 1), compare_end_date=date(2026, 10, 31)),
        "compare o tomate deste mês com o passado", TODAY,
    )

    output = AssistantTools(queries, search=None).run(plan, SimpleNamespace(account_id=uuid4()), TODAY)

    assert output.data["comparacao"]["periodo"] == "de 01/10/2026 a 31/10/2026"
    assert ("items", None, date(2026, 10, 1)) in queries.calls


def test_price_changes_compare_first_and_last_purchase():
    from types import SimpleNamespace

    from services.assistant.tools import AssistantTools, _product_key

    rows = [
        item_row("FLOCAO DE MILHO CORIN 1,000", date(2026, 11, 1), "2.55"),
        item_row("TOMATE LONGA VIDA KG", date(2026, 11, 6), "12.90"),
        item_row("ALFACE COMUM MC", date(2026, 11, 1), "4.19"),
        item_row("ALFACE COMUM MC", date(2026, 11, 11), "4.19"),
        item_row("TOMATE LONGA VIDA kg", date(2026, 11, 11), "8.95"),
        item_row("FLOCAO DE MILHO CORIN", date(2026, 11, 11), "2.59"),
        item_row("SO UMA VEZ", date(2026, 11, 11), "1.00"),
    ]

    output = AssistantTools(FakeQueries(rows=rows), search=None).run(
        QueryPlan(intent=Intent.PRICE_CHANGES), SimpleNamespace(account_id=uuid4()), TODAY
    )

    assert _product_key("TOMATE LONGA VIDA kg") == _product_key("TOMATE LONGA VIDA KG") == "TOMATE LONGA VIDA"
    up, down = output.data["ficaram_mais_caros"], output.data["ficaram_mais_baratos"]
    assert [(c["produto"], c["variacao"]) for c in up] == [("FLOCAO DE MILHO CORIN", "+1,6%")]
    assert [(c["produto"], c["variacao"]) for c in down] == [("TOMATE LONGA VIDA kg", "-30,6%")]
    assert (output.data["mesmo_preco"], output.data["produtos_comparados"]) == (1, 3)
    assert output.data["gasto_total_no_filtro"] == "R$ 422,45"  # "how much did I spend ... and what got more expensive?"


def test_a_definition_repeats_the_previous_plan():
    from services.assistant.planner import clarifies_previous

    previous = {"intent": "price_history", "product": "legumes", "start_date": "2026-11-01", "end_date": "2026-11-30"}

    repeated = clarifies_previous(previous, "Legumes são tomates, cebolas e etc")

    assert (repeated.intent, repeated.product, repeated.start_date) == (Intent.PRICE_HISTORY, "legumes", date(2026, 11, 1))
    assert clarifies_previous(previous, "obrigado!") is None
    assert clarifies_previous(None, "legumes") is None


@pytest.mark.parametrize(
    ("text", "broken"),
    [
        ("analysis:We have a user query", True),
        ("Águas de Teres " + "Ter " * 40, True),
        ("| Produto | Fonte |\n|---|---|\n" + "| X | [D1] |\n" * 40, False),
        ("Você gastou R$ 50,77 com frango [D1].", False),
    ],
)
def test_degenerate_answers_are_model_failures(text, broken):
    from services.llm_router import is_degenerate

    assert is_degenerate(text) is broken


def test_answer_without_citations_lists_the_sources():
    sources = [Source(ref="D1", document_id=uuid4(), original_filename="a.jpg")]

    cleaned, citations = resolve_citations("Você gastou R$ 369,12.", sources, uuid4())

    assert cleaned.endswith("Fontes: [D1]")
    assert [c["ref"] for c in citations] == ["D1"]
