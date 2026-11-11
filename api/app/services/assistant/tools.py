"""Assistant tools: fixed SQL (and semantic search), never SQL from the LLM.

Each tool returns:
- `data`: already computed and formatted facts (the LLM only writes them up);
- `sources`: documents that support the facts, with references D1, D2...
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from core.account_context import AccountContext
from core.exceptions import ServiceUnavailableError
from repositories.financial_queries import FinancialQueryRepository
from services.assistant.items import ItemResolver, plain
from services.assistant.planner import Intent, QueryPlan, bills_since
from services.indexing import DOCUMENT_TYPE_LABELS, PAYMENT_STATUS_LABELS, format_brl
from services.search import SearchService

logger = logging.getLogger(__name__)

MAX_BILLS = 12


@dataclass(frozen=True)
class Source:
    ref: str
    document_id: UUID
    original_filename: str


@dataclass
class ToolOutput:
    tool: str
    data: dict[str, Any]
    sources: list[Source] = field(default_factory=list)

    @property
    def has_results(self) -> bool:
        return bool(self.data.get("has_results"))


class _SourceRegistry:
    def __init__(self) -> None:
        self._by_document: dict[UUID, Source] = {}

    def ref(self, document_id: UUID | None, filename: str | None) -> str | None:
        if document_id is None:
            return None
        if document_id not in self._by_document:
            ref = f"D{len(self._by_document) + 1}"
            self._by_document[document_id] = Source(ref, document_id, filename or "documento")
        return self._by_document[document_id].ref

    @property
    def sources(self) -> list[Source]:
        return list(self._by_document.values())


def _date(value: date | None) -> str | None:
    return value.strftime("%d/%m/%Y") if value else None


def _period(plan: QueryPlan) -> str:
    return _span(plan.start_date, plan.end_date)


def _span(start: date | None, end: date | None) -> str:
    if start and end:
        return f"de {_date(start)} a {_date(end)}"
    if start:
        return f"a partir de {_date(start)}"
    if end:
        return f"até {_date(end)}"
    return "todo o histórico"


def _has_comparison(plan: QueryPlan) -> bool:
    return bool(plan.compare_start_date and plan.compare_end_date)


def _types_label(plan: QueryPlan) -> str | None:
    return ", ".join(DOCUMENT_TYPE_LABELS.get(t.value, t.value) for t in plan.document_types) or None


def _bill_status(status: str, due_date: date | None, today: date) -> str:
    """'to pay' with a past due date is 'overdue': the photo does not say whether it was paid."""
    label = PAYMENT_STATUS_LABELS.get(status, status)
    if due_date and due_date < today and status in ("pending", "unknown"):
        return f"vencido em {_date(due_date)} (não há registro de pagamento)"
    return label


def _quantity(value: Decimal | None) -> str | None:
    return None if value is None else str(value.normalize())


@dataclass(frozen=True)
class _ItemChoice:
    """Items chosen by name (None = text search) and the names the choice evaluated."""

    descriptions: list[str] | None = None
    seen: list[str] | None = None

    def query_args(self) -> dict[str, Any]:
        return {"descriptions": self.descriptions, "seen": self.seen}


MAX_LISTED_ITEMS = 20
MAX_DOCUMENTS_WITH_ITEMS = 3
MAX_ITEMS_PER_DOCUMENT = 40
MAX_PRICE_CHANGES = 8

# "TOMATE LONGA VIDA KG" and "TOMATE LONGA VIDA kg", "FLOCAO DE MILHO CORIN 1,000" and "FLOCAO DE
# MILHO CORIN" are the same product: unit and loose numbers at the end are not compared.
_TRAILING_NOISE = re.compile(r"(\s+(KG|UN|UND|G|L|ML|CX|PCT|[\d.,]+))+$")


def _product_key(description: str) -> str:
    return _TRAILING_NOISE.sub("", plain(description))


class AssistantTools:
    def __init__(
        self, queries: FinancialQueryRepository, search: SearchService, resolver: ItemResolver | None = None
    ) -> None:
        self.queries = queries
        self.search = search
        self.resolver = resolver

    def run(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        handlers = {
            Intent.SPENDING_SUMMARY: self._spending_summary,
            Intent.PRICE_HISTORY: self._price_history,
            Intent.LAST_PURCHASE: self._last_purchase,
            Intent.TOP_ITEMS: self._top_items,
            Intent.PRICE_CHANGES: self._price_changes,
            Intent.BILLS_COMPARISON: self._bills_comparison,
            Intent.LIST_DOCUMENTS: self._list_documents,
            Intent.DOCUMENT_SEARCH: self._document_search,
        }
        handler = handlers.get(plan.intent)
        if handler is None:
            return ToolOutput(tool="none", data={"has_results": False})
        output = handler(plan, context, today)
        if not output.data.get("has_results") and plan.intent in (Intent.PRICE_HISTORY, Intent.LAST_PURCHASE):
            # No item with that name (e.g. "cleaning products", "fruit" are categories):
            # semantic search over the documents finds soap, detergent, banana...
            fallback = self._document_search(plan, context, today)
            if fallback.data.get("has_results"):
                fallback.data["observacao"] = (
                    f"Nenhum item com o nome '{plan.product}'. Trechos dos documentos relacionados ao tema:"
                )
                output = fallback
        return self._with_pending_review(output, context)

    def _with_pending_review(self, output: ToolOutput, context: AccountContext) -> ToolOutput:
        """Receipts with a doubtful reading are left out of the numbers: the writer mentions it in one sentence."""
        pending = self.queries.pending_review_count(context.account_id)
        if pending:
            output.data["notas_pendentes_de_revisao"] = pending
        return output

    def _spending_summary(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        refs = _SourceRegistry()
        summary = self.queries.spending_summary(
            context.account_id,
            start=plan.start_date,
            end=plan.end_date,
            document_types=[t.value for t in plan.document_types] or None,
            establishment=plan.establishment,
        )
        data = {
            "has_results": summary.count > 0,
            "periodo": _period(plan),
            "tipos": _types_label(plan),
            "estabelecimento_filtrado": plan.establishment,
            "total_gasto": format_brl(summary.total),
            "quantidade_de_lancamentos": summary.count,
            "por_estabelecimento": [
                {"estabelecimento": e.establishment, "total": format_brl(e.total), "lancamentos": e.count}
                for e in summary.by_establishment
            ],
            "maiores_lancamentos": [
                {
                    "fonte": refs.ref(row.document_id, row.original_filename),
                    "descricao": row.description,
                    "estabelecimento": row.establishment,
                    "valor": format_brl(row.amount),
                    "data": _date(row.occurred_at),
                }
                for row in summary.largest
            ],
        }
        if _has_comparison(plan):
            other = self.queries.spending_summary(
                context.account_id,
                start=plan.compare_start_date,
                end=plan.compare_end_date,
                document_types=[t.value for t in plan.document_types] or None,
                establishment=plan.establishment,
            )
            data["comparacao"] = {
                "periodo": _span(plan.compare_start_date, plan.compare_end_date),
                "total_gasto": format_brl(other.total),
                "quantidade_de_lancamentos": other.count,
                "diferenca": format_brl(summary.total - other.total),
            }
            data["has_results"] = data["has_results"] or other.count > 0
        return ToolOutput("spending_summary", data, refs.sources)

    def _choose_items(self, plan: QueryPlan, context: AccountContext) -> _ItemChoice:
        """Chooses, from the account vocabulary, the items that are the product (chicken → CSCOX...).
        Without AI available (or without a choice), text search is used."""
        if self.resolver is None or not plan.product:
            return _ItemChoice()
        # Vocabulary of the period (and of the comparison period, which only exists with the main one closed).
        start = min(d for d in (plan.start_date, plan.compare_start_date) if d) if plan.start_date else None
        end = max(d for d in (plan.end_date, plan.compare_end_date) if d) if plan.end_date else None
        names, truncated = self.queries.item_vocabulary(context.account_id, start=start, end=end)
        if not names:
            return _ItemChoice()
        chosen = self.resolver.select(plan.product, plan.search_query or plan.product, names)
        if not chosen:
            # AI unavailable, or nothing chosen: text search may still find something.
            return _ItemChoice()
        return _ItemChoice(descriptions=chosen, seen=names if truncated else None)

    def _item_rows(
        self, plan: QueryPlan, context: AccountContext, choice: _ItemChoice, order: str, start, end, refs: _SourceRegistry
    ) -> list[dict]:
        rows = self.queries.items_matching(
            context.account_id,
            plan.product or "",
            start=start,
            end=end,
            order=order,
            limit=MAX_LISTED_ITEMS,
            **choice.query_args(),
        )
        return [
            {
                "fonte": refs.ref(row.document_id, row.original_filename),
                "produto": row.description,
                "estabelecimento": row.establishment,
                "data": _date(row.occurred_at),
                "quantidade": _quantity(row.quantity),
                "unidade": row.unit,
                "preco_unitario": format_brl(row.unit_price),
                "total": format_brl(row.total_price),
            }
            for row in rows
        ]

    def _items(
        self, plan: QueryPlan, context: AccountContext, order: str, choice: _ItemChoice | None = None
    ) -> tuple[list[dict], _SourceRegistry]:
        refs = _SourceRegistry()
        choice = choice if choice is not None else self._choose_items(plan, context)
        return self._item_rows(plan, context, choice, order, plan.start_date, plan.end_date, refs), refs

    def _totals(self, plan: QueryPlan, context: AccountContext, choice: _ItemChoice, start, end) -> dict:
        totals = self.queries.product_totals(
            context.account_id, plan.product or "", start=start, end=end, **choice.query_args()
        )
        return {
            "vezes_comprado": totals.purchases,
            "total_gasto": format_brl(totals.total_spent),
            "quantidade_total": {unit: _quantity(qty) for unit, qty in totals.quantity_by_unit.items()},
        }

    def _price_history(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        choice = self._choose_items(plan, context)
        items, refs = self._items(plan, context, order="cheapest", choice=choice)
        data = {
            "has_results": bool(items),
            "produto_buscado": plan.product,
            "periodo": _period(plan),
            # Sums of ALL purchases of the product (done in the database), not only the listed ones.
            "resumo": self._totals(plan, context, choice, plan.start_date, plan.end_date),
            "ordenado_por": "menor preço unitário primeiro",
            "mais_barato": items[0] if items else None,
            "compras": items,
        }
        if choice.descriptions:
            # What was counted as the product: the user can check it ("frango" = CSCOX LAR C/DORSO).
            data["itens_considerados"] = sorted({item["produto"] for item in items})
        if _has_comparison(plan):
            compared = self._item_rows(
                plan, context, choice, "cheapest", plan.compare_start_date, plan.compare_end_date, refs
            )
            data["comparacao"] = {
                "periodo": _span(plan.compare_start_date, plan.compare_end_date),
                "resumo": self._totals(plan, context, choice, plan.compare_start_date, plan.compare_end_date),
                "compras": compared,
            }
            data["has_results"] = bool(items or compared)
        return ToolOutput("price_history", data, refs.sources)

    def _top_items(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        refs = _SourceRegistry()
        rows = self.queries.top_items(
            context.account_id,
            start=plan.start_date,
            end=plan.end_date,
            document_types=[t.value for t in plan.document_types] or None,
            establishment=plan.establishment,
        )
        items = [
            {
                "fonte": refs.ref(row.document_id, row.original_filename),
                "produto": row.description,
                "estabelecimento": row.establishment,
                "data": _date(row.occurred_at),
                "quantidade": _quantity(row.quantity),
                "unidade": row.unit,
                "preco_unitario": format_brl(row.unit_price),
                "total": format_brl(row.total_price),
            }
            for row in rows
        ]
        products = [
            {
                "produto": product.description,
                "vezes_na_nota": product.purchases,
                "total_gasto": format_brl(product.total_spent),
                "fontes": [refs.ref(doc_id, filename) for doc_id, filename in product.documents],
            }
            for product in self.queries.top_products(
                context.account_id,
                start=plan.start_date,
                end=plan.end_date,
                document_types=[t.value for t in plan.document_types] or None,
                establishment=plan.establishment,
                limit=5,
            )
        ]
        data = {
            "has_results": bool(items),
            "periodo": _period(plan),
            "tipos": _types_label(plan),
            "estabelecimento_filtrado": plan.establishment,
            "como_usar": (
                "'itens mais caros' → use 'itens' (cada linha da nota, maior valor primeiro). "
                "'com o que mais gastei' → use 'gasto_por_produto' (mesmo produto somado). "
                "Mostre até 5."
            ),
            "itens": items,
            "gasto_por_produto": products,
        }
        return ToolOutput("top_items", data, refs.sources)

    def _last_purchase(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        items, refs = self._items(plan, context, order="latest")
        data = {
            "has_results": bool(items),
            "produto_buscado": plan.product,
            "ultima_compra": items[0] if items else None,
            "compras_recentes": items[:5],
        }
        return ToolOutput("last_purchase", data, refs.sources)

    def _bills_comparison(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        refs = _SourceRegistry()
        bill_types = [t.value for t in plan.document_types]
        if plan.start_date or plan.end_date:
            since, until = plan.start_date, plan.end_date
        elif plan.months:
            since, until = bills_since(plan.months, today), None
        else:
            since, until = None, None  # full history (limited to the most recent)
        rows = self.queries.bills_by_month(context.account_id, bill_types, since=since, until=until)
        rows = rows[-MAX_BILLS * len(bill_types) :]
        several = len(bill_types) > 1

        bills = [
            {
                "fonte": refs.ref(row.document_id, row.original_filename),
                "tipo": DOCUMENT_TYPE_LABELS.get(row.document_type or "", row.document_type),
                "mes_referencia": row.occurred_at.strftime("%m/%Y"),
                "emissao": _date(row.occurred_at),
                "vencimento": _date(row.due_date),
                "valor": format_brl(row.amount),
                "situacao": _bill_status(row.payment_status, row.due_date, today),
                "emissor": row.establishment or "não identificado na foto",
            }
            for row in rows
        ]
        amounts = [row.amount for row in rows]
        data = {
            "has_results": bool(rows),
            "tipos": [DOCUMENT_TYPE_LABELS.get(t, t) for t in bill_types],
            "a_partir_de": _date(since) or "início do histórico",
            "ate": _date(until) or "hoje",
            "contas": bills,
            "total_no_periodo": format_brl(sum(amounts, Decimal("0"))) if amounts else None,
        }
        if several:
            # Average/highest/lowest mixing water and electricity mean nothing: only the latest of each type.
            latest = {bill["tipo"]: bill for bill in bills}
            data["ultima_conta_de_cada_tipo"] = list(latest.values())
            missing = [DOCUMENT_TYPE_LABELS.get(t, t) for t in bill_types if DOCUMENT_TYPE_LABELS.get(t, t) not in latest]
            data["tipos_sem_conta_enviada"] = missing
        elif amounts:
            data.update(
                {
                    "ultima_conta": bills[-1],
                    "media_mensal": format_brl(sum(amounts, Decimal("0")) / len(amounts)),
                    "maior": format_brl(max(amounts)),
                    "menor": format_brl(min(amounts)),
                }
            )
        return ToolOutput("bills_comparison", data, refs.sources)

    def _list_documents(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        refs = _SourceRegistry()
        rows = self.queries.list_documents(
            context.account_id,
            start=plan.start_date,
            end=plan.end_date,
            document_types=[t.value for t in plan.document_types] or None,
            establishment=plan.establishment,
        )
        # "Mostra os produtos da nota do Sendas": the items of the first receipts come along.
        items = self.queries.items_of_documents(
            context.account_id, [row.document_id for row in rows[:MAX_DOCUMENTS_WITH_ITEMS]]
        )
        documents = [
            {
                "fonte": refs.ref(row.document_id, row.original_filename),
                "arquivo": row.original_filename,
                "tipo": DOCUMENT_TYPE_LABELS.get(row.document_type or "", row.document_type or "não identificado"),
                "data": _date(row.occurred_at) or _date(row.created_at.date()),
                "valor": format_brl(row.amount),
                "estabelecimento": row.establishment,
                "enviado_em": _date(row.created_at.date()),
                "processamento": row.processing_status,
                **(
                    {
                        "itens": [
                            {
                                "produto": item.description,
                                "quantidade": _quantity(item.quantity),
                                "unidade": item.unit,
                                "preco_unitario": format_brl(item.unit_price),
                                "total": format_brl(item.total_price),
                            }
                            for item in items[row.document_id][:MAX_ITEMS_PER_DOCUMENT]
                        ]
                    }
                    if row.document_id in items
                    else {}
                ),
            }
            for row in rows
        ]
        data = {
            "has_results": bool(documents),
            "periodo": _period(plan),
            "tipos": _types_label(plan),
            "estabelecimento_filtrado": plan.establishment,
            "ordenado_por": "envio mais recente primeiro",
            "documentos": documents,
        }
        return ToolOutput("list_documents", data, refs.sources)

    def _price_changes(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        """Product bought on different dates: unit price of the first vs the last purchase."""
        refs = _SourceRegistry()
        rows = self.queries.price_series(
            context.account_id,
            start=plan.start_date,
            end=plan.end_date,
            document_types=[t.value for t in plan.document_types] or None,
            establishment=plan.establishment,
        )
        series: dict[str, list] = {}
        for row in rows:
            series.setdefault(_product_key(row.description), []).append(row)

        changes = []
        for purchases in series.values():
            first, last = purchases[0], purchases[-1]
            if first.occurred_at == last.occurred_at or not first.unit_price:
                continue
            percent = (last.unit_price - first.unit_price) / first.unit_price * 100
            changes.append((percent, first, last, len(purchases)))

        def describe(percent: Decimal, first, last, count: int) -> dict:
            return {
                "produto": last.description,
                "antes": {
                    "preco_unitario": format_brl(first.unit_price),
                    "data": _date(first.occurred_at),
                    "estabelecimento": first.establishment,
                    "fonte": refs.ref(first.document_id, first.original_filename),
                },
                "depois": {
                    "preco_unitario": format_brl(last.unit_price),
                    "data": _date(last.occurred_at),
                    "estabelecimento": last.establishment,
                    "fonte": refs.ref(last.document_id, last.original_filename),
                },
                "variacao": f"{percent:+.1f}%".replace(".", ","),
                "compras": count,
            }

        changes.sort(key=lambda c: c[0], reverse=True)
        # "Quanto gastei no mercado e o que ficou mais caro?": the total for the same filter comes along.
        spent = self.queries.spending_summary(
            context.account_id,
            start=plan.start_date,
            end=plan.end_date,
            document_types=[t.value for t in plan.document_types] or None,
            establishment=plan.establishment,
        )
        data = {
            "has_results": bool(changes),
            "periodo": _period(plan),
            "gasto_total_no_filtro": format_brl(spent.total),
            "lancamentos_no_filtro": spent.count,
            "tipos": _types_label(plan),
            "estabelecimento_filtrado": plan.establishment,
            "como_foi_calculado": (
                "Só produtos comprados em pelo menos duas datas: preço unitário da primeira compra x da última "
                "(lojas podem ser diferentes)."
            ),
            "como_responder": (
                "Mostre os que ficaram mais caros (produto, antes → depois, variação). Se a pergunta for "
                "sobre os mais caros e nenhum subiu, diga isso e mostre os que ficaram mais baratos."
            ),
            "ficaram_mais_caros": [describe(*c) for c in changes if c[0] > 0][:MAX_PRICE_CHANGES],
            "ficaram_mais_baratos": [describe(*c) for c in reversed(changes) if c[0] < 0][:MAX_PRICE_CHANGES],
            "mesmo_preco": len([c for c in changes if c[0] == 0]),
            "produtos_comparados": len(changes),
        }
        return ToolOutput("price_changes", data, refs.sources)

    def _document_search(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        refs = _SourceRegistry()
        try:
            response = self.search.search(context, plan.search_query or "", limit=5)
        except ServiceUnavailableError:
            logger.warning("Semantic search unavailable for assistant")
            return ToolOutput("document_search", {"has_results": False, "aviso": "busca nos documentos indisponível"})

        snippets = [
            {
                "fonte": refs.ref(result.document_id, result.original_filename),
                "arquivo": result.original_filename,
                "trecho": result.content[:1200],
            }
            for result in response.results
        ]
        return ToolOutput("document_search", {"has_results": bool(snippets), "trechos": snippets}, refs.sources)
