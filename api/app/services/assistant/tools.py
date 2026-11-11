"""Assistant tools: fixed SQL (and semantic search), never SQL from the LLM.

Each tool returns:
- `data`: already computed and formatted facts (the LLM only writes them up);
- `sources`: documents that support the facts, with references D1, D2...
"""

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from core.account_context import AccountContext
from core.exceptions import ServiceUnavailableError
from repositories.financial_queries import FinancialQueryRepository
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
    if plan.start_date and plan.end_date:
        return f"de {_date(plan.start_date)} a {_date(plan.end_date)}"
    if plan.start_date:
        return f"a partir de {_date(plan.start_date)}"
    if plan.end_date:
        return f"até {_date(plan.end_date)}"
    return "todo o histórico"


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


class AssistantTools:
    def __init__(self, queries: FinancialQueryRepository, search: SearchService) -> None:
        self.queries = queries
        self.search = search

    def run(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        handlers = {
            Intent.SPENDING_SUMMARY: self._spending_summary,
            Intent.PRICE_HISTORY: self._price_history,
            Intent.LAST_PURCHASE: self._last_purchase,
            Intent.TOP_ITEMS: self._top_items,
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
                return fallback
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
        return ToolOutput("spending_summary", data, refs.sources)

    def _items(self, plan: QueryPlan, context: AccountContext, order: str) -> tuple[list[dict], _SourceRegistry]:
        refs = _SourceRegistry()
        rows = self.queries.items_matching(
            context.account_id, plan.product or "", start=plan.start_date, end=plan.end_date, order=order, limit=10
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
        return items, refs

    def _price_history(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        items, refs = self._items(plan, context, order="cheapest")
        totals = self.queries.product_totals(
            context.account_id, plan.product or "", start=plan.start_date, end=plan.end_date
        )
        data = {
            "has_results": bool(items),
            "produto_buscado": plan.product,
            "periodo": _period(plan),
            # Sums of ALL purchases of the product (done in the database), not only the listed ones.
            "resumo": {
                "vezes_comprado": totals.purchases,
                "total_gasto": format_brl(totals.total_spent),
                "quantidade_total": {unit: _quantity(qty) for unit, qty in totals.quantity_by_unit.items()},
            },
            "ordenado_por": "menor preço unitário primeiro",
            "mais_barato": items[0] if items else None,
            "compras": items,
        }
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
        bill_type = plan.document_types[0].value
        if plan.start_date or plan.end_date:
            since, until = plan.start_date, plan.end_date
        elif plan.months:
            since, until = bills_since(plan.months, today), None
        else:
            since, until = None, None  # full history (limited to the most recent)
        rows = self.queries.bills_by_month(context.account_id, bill_type, since=since, until=until)
        rows = rows[-MAX_BILLS:]

        bills = [
            {
                "fonte": refs.ref(row.document_id, row.original_filename),
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
            "tipo": DOCUMENT_TYPE_LABELS.get(bill_type, bill_type),
            "a_partir_de": _date(since) or "início do histórico",
            "ate": _date(until) or "hoje",
            "contas": bills,
            "ultima_conta": bills[-1] if bills else None,
            "total_no_periodo": format_brl(sum(amounts, Decimal("0"))) if amounts else None,
            "media_mensal": format_brl(sum(amounts, Decimal("0")) / len(amounts)) if amounts else None,
            "maior": format_brl(max(amounts)) if amounts else None,
            "menor": format_brl(min(amounts)) if amounts else None,
        }
        return ToolOutput("bills_comparison", data, refs.sources)

    def _list_documents(self, plan: QueryPlan, context: AccountContext, today: date) -> ToolOutput:
        refs = _SourceRegistry()
        rows = self.queries.list_documents(
            context.account_id,
            start=plan.start_date,
            end=plan.end_date,
            document_types=[t.value for t in plan.document_types] or None,
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
            }
            for row in rows
        ]
        data = {
            "has_results": bool(documents),
            "periodo": _period(plan),
            "tipos": _types_label(plan),
            "ordenado_por": "envio mais recente primeiro",
            "documentos": documents,
        }
        return ToolOutput("list_documents", data, refs.sources)

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
