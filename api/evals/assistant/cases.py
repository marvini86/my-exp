"""Evaluation questions with the expected answers (computed from dataset.py, today = 2026-11-09).

Text check (accent and case insensitive; "369,12" also accepts "369.12"):
- include:      everything must appear
- include_any:  at least one must appear
- exclude:      none of it may appear (e.g. a number from another period)
- intent:       intent expected from the planner (diagnostic; does not fail on its own)
- citations:    minimum number of [D#] sources cited
Each case is a conversation; several turns test follow-up questions.
Questions are in Portuguese, like the product.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Turn:
    question: str
    include: tuple[str, ...] = ()
    include_any: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    intent: str | None = None
    citations: int = 0


@dataclass(frozen=True)
class Case:
    id: str
    turns: tuple[Turn, ...]
    tags: tuple[str, ...] = field(default=())


def ask(question: str, **expected) -> Turn:
    for key in ("include", "include_any", "exclude"):
        if isinstance(expected.get(key), str):
            expected[key] = (expected[key],)
        elif key in expected:
            expected[key] = tuple(expected[key])
    return Turn(question, **expected)


CASES: list[Case] = [
    # --- Spending by period and type -----------------------------------------------------
    Case("mercado-este-mes", (ask("quanto gastei no mercado este mês?", include="369,12", intent="spending_summary", citations=1),), ("gastos",)),
    Case("mercado-ontem", (ask("quanto gastei ontem no supermercado?", include="162,95", exclude="369,12", citations=1),), ("gastos", "datas")),
    Case("hoje-sem-compras", (ask("quanto gastei hoje?", include_any=("nenhum", "nao ha", "nao encontrei", "0,00", "nao houve", "nao tem"), exclude=("162,95", "369,12")),), ("gastos", "datas")),
    Case("mercado-outubro", (ask("quanto gastei de mercado em outubro?", include="69,73", exclude="369,12"),), ("gastos",)),
    Case("total-outubro", (ask("quanto gastei no total em outubro, contando tudo?", include="813,55"),), ("gastos",)),
    Case("maior-compra-mes", (ask("qual foi minha maior compra este mês?", include="123,83", include_any=("rcarvalho", "01/11")),), ("gastos",)),
    Case("por-mercado-mes", (ask("quanto gastei em cada mercado este mês?", include=("226,29", "112,15", "30,68")),), ("gastos",)),
    Case("no-sendas", (ask("quanto gastei no Sendas?", include="112,15", exclude="226,29"),), ("gastos", "estabelecimento")),
    Case("farmacia", (ask("quanto gastei na farmácia?", include="46,74"),), ("gastos", "tipo")),
    Case(
        "continuacao-mes",
        (
            ask("quanto gastei no mercado em outubro?", include="69,73"),
            ask("e em novembro?", include="369,12", exclude="69,73"),
        ),
        ("gastos", "continuacao"),
    ),
    # --- Produtos -----------------------------------------------------------------------
    Case("carne-mes", (ask("quanto gastei com carne este mês?", include="87,57", intent="price_history"),), ("produto",)),
    Case(
        "carne-continuacao",
        (
            ask("quanto gastei com carne este mês?", include="87,57"),
            ask("e em todas as notas, independente do mês?", include="134,32"),
        ),
        ("produto", "continuacao"),
    ),
    Case("tomate-mais-barato", (ask("onde o tomate está mais barato?", include=("10,90", "sendas")),), ("produto", "preco")),
    Case("leite-mais-barato", (ask("qual o leite mais barato que comprei?", include="5,49"),), ("produto", "preco")),
    Case("banana-total", (ask("quanto gastei com banana?", include="6,91"),), ("produto",)),
    Case("ovos-ultima", (ask("quando comprei ovos pela última vez?", include="06/11", intent="last_purchase"),), ("produto",)),
    Case("cafe-quando", (ask("quando comprei café?", include="31/10", include_any=("10,25",)),), ("produto",)),
    Case("castanha", (ask("quanto paguei na castanha de caju?", include="27,74"),), ("produto", "abreviacao")),
    Case("nota-do-tomate", (ask("me dá a nota em que comprei tomate salada", include_any=("sendas", "rcarvalho"), citations=1),), ("produto", "documento")),
    Case("limpeza", (ask("quais produtos de limpeza eu comprei?", include_any=("detergente", "urca", "sabte", "sabonete")),), ("produto", "categoria")),
    # --- Rankings -----------------------------------------------------------------------
    Case("itens-caros-ontem", (ask("quais itens foram mais caros na compra de ontem?", include="43,58", exclude="53,04", intent="top_items"),), ("ranking",)),
    Case("mais-gastei-mes", (ask("com o que mais gastei este mês?", include=("53,04", "50,77"), intent="top_items"),), ("ranking",)),
    # --- Bills -------------------------------------------------------------------------
    Case("agua", (ask("qual o valor da conta de água e quando vence?", include=("131,42", "12/11")),), ("contas",)),
    Case("luz-outubro", (ask("quanto paguei de luz em outubro?", include="612,40", exclude="729,08"),), ("contas",)),
    Case("luz-comparar", (ask("compare minhas contas de luz", include=("612,40", "729,08")),), ("contas",)),
    Case("luz-vencida", (ask("tenho alguma conta de luz para pagar?", include=("729,08", "vencid")),), ("contas", "vencimento")),
    Case("contas-da-casa", (ask("quanto gastei com contas da casa em outubro?", include="743,82"),), ("contas", "categoria")),
    # --- Document details (semantic search) ----------------------------------------
    Case("desconto-sendas", (ask("qual foi o desconto na compra do Sendas?", include="3,78"),), ("documento",)),
    Case("cnpj-sendas", (ask("qual o CNPJ do Sendas?", include="06.057.223/0461-63"),), ("documento",)),
    Case("documentos", (ask("quais documentos eu enviei?", include_any=("sendas", "rcarvalho"), intent="list_documents"),), ("documento",)),
    # --- Out of scope -----------------------------------------------------------------------
    Case("fora-escopo", (ask("qual a capital da França?", exclude=("r$", "paris"), intent="out_of_scope"),), ("escopo",)),
]
