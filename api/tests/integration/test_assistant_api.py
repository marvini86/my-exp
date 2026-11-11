"""Assistant API with a fake LLM (flow, persistence, privacy, errors)."""

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from database.tenant_context import set_account_context
from services.assistant.planner import Intent, QueryPlan
from services.llm_router import LLMOutputError, LLMUnavailableError
from test_financial_queries import add_purchase

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("dev_environment")]


class FakeLLMRouter:
    def __init__(self) -> None:
        self.plans: list = []
        self.answers: list = []
        self.item_choices: list[list[int]] = []
        self.calls: list[str] = []

    def structured(self, messages, schema, *, needs_vision=False):
        if schema is not QueryPlan:
            # Item selector: with no scripted choice, the AI "did not answer" (text search is used).
            self.calls.append("items")
            if not self.item_choices:
                raise LLMOutputError("no item choice scripted")
            return schema(matches=self.item_choices.pop(0)), "fake/items"
        self.calls.append("plan")
        item = self.plans.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, "fake/planner"

    def text(self, messages):
        self.calls.append("answer")
        self.last_answer_prompt = messages[1].content
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, "fake/writer"


class NoSearch:
    def embed_query(self, text):
        from services.embeddings import EmbeddingUnavailableError

        raise EmbeddingUnavailableError("disabled in tests")


@pytest.fixture
def llm(client):
    from main import app
    from routes.conversation import get_llm_router
    from services.search import get_embedding_router, get_vector_store

    fake = FakeLLMRouter()
    app.dependency_overrides[get_llm_router] = lambda: fake
    app.dependency_overrides[get_embedding_router] = lambda: NoSearch()
    app.dependency_overrides[get_vector_store] = lambda: None
    return fake


@pytest.fixture
def family(client, make_user, session):
    owner_id, owner = make_user("as-owner@example.com")
    _, member = make_user("as-member@example.com")
    _, outsider = make_user("as-outsider@example.com")
    account_id = client.post("/accounts/", json={"name": "Família", "slug": "assist"}, headers=owner).json()["id"]
    client.post(f"/accounts/{account_id}/members/", json={"email": "as-member@example.com"}, headers=owner)

    set_account_context(session, UUID(account_id), UUID(owner_id))
    doc = add_purchase(
        session, UUID(account_id), doc_type="nfce", establishment="Mercado Exemplo", amount="63.43",
        day=date(2026, 10, 10), items=[("LEITE INTEGRAL 1L", "4.89", "6")], filename="cupom.xml",
    )
    return {"id": account_id, "owner": owner, "member": member, "outsider": outsider, "doc": doc}


def new_conversation(client, family, who="owner"):
    response = client.post(f"/accounts/{family['id']}/conversations/", json={}, headers=family[who])
    assert response.status_code == 201
    return response.json()["id"]


def ask(client, family, conversation_id, content, who="owner"):
    return client.post(
        f"/accounts/{family['id']}/conversations/{conversation_id}/messages",
        json={"content": content},
        headers=family[who],
    )


def test_price_question_end_to_end(client, llm, family):
    conversation = new_conversation(client, family)
    llm.plans.append(QueryPlan(intent=Intent.PRICE_HISTORY, product="leite"))
    llm.answers.append("O leite mais barato foi R$ 4,89 no Mercado Exemplo [D1]. Também vi [D7].")

    response = ask(client, family, conversation, "onde comprei leite mais barato?")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_message"]["content"] == "onde comprei leite mais barato?"
    answer = body["assistant_message"]
    assert answer["content"] == "O leite mais barato foi R$ 4,89 no Mercado Exemplo [D1]. Também vi."
    assert [c["original_filename"] for c in answer["citations"]] == ["cupom.xml"]
    assert answer["citations"][0]["document_id"] == str(family["doc"].id)
    # The LLM received the facts already computed by SQL.
    assert "R$ 4,89" in llm.last_answer_prompt and "Mercado Exemplo" in llm.last_answer_prompt


def test_product_chosen_from_the_account_vocabulary(client, llm, family, session):
    """'frango' (chicken) is not in the item name: the AI choice over the account vocabulary finds it."""
    add_purchase(session, UUID(family["id"]), doc_type="nfce", establishment="SENDAS", amount="18.24",
                 day=date(2026, 10, 12), items=[("CSCOX LAR C/DORSO", "8.19", "1"), ("CR LEITE TP 200G", "2.99", "1")])
    conversation = new_conversation(client, family)
    llm.plans.append(QueryPlan(intent=Intent.PRICE_HISTORY, product="frango"))
    # Vocabulary: most recent first, then alphabetical → 1 = CR LEITE TP 200G, 2 = CSCOX LAR C/DORSO.
    llm.item_choices.append([2])
    llm.answers.append("Frango: R$ 8,19 [D1].")

    ask(client, family, conversation, "quanto gastei com frango?")

    assert llm.calls == ["plan", "items", "answer"]
    assert "CSCOX LAR C/DORSO" in llm.last_answer_prompt and "itens_considerados" in llm.last_answer_prompt
    assert "CR LEITE" not in llm.last_answer_prompt


def test_follow_up_planner_receives_the_previous_plan(client, llm, family):
    conversation = new_conversation(client, family)
    llm.plans += [QueryPlan(intent=Intent.PRICE_HISTORY, product="legumes", start_date=date(2026, 10, 1),
                            end_date=date(2026, 10, 31)), QueryPlan(intent=Intent.SPENDING_SUMMARY)]
    llm.answers += ["Nada.", "Ok."]
    captured = []
    original = llm.structured

    def spy(messages, schema, **kwargs):
        if schema is QueryPlan:
            captured.append(messages[1].content)
        return original(messages, schema, **kwargs)

    llm.structured = spy
    ask(client, family, conversation, "qt gastei de legumes em outubro?")
    ask(client, family, conversation, "legumes são tomate, cebola etc")

    assert '"product": "legumes"' in captured[1] and '"start_date": "2026-10-01"' in captured[1]


def test_messages_are_persisted_and_conversation_titled(client, llm, family):
    conversation = new_conversation(client, family)
    llm.plans.append(QueryPlan(intent=Intent.SPENDING_SUMMARY, start_date=date(2026, 10, 1), end_date=date(2026, 10, 31)))
    llm.answers.append("Você gastou R$ 63,43 em outubro [D1].")

    ask(client, family, conversation, "quanto gastei em outubro?")

    messages = client.get(f"/accounts/{family['id']}/conversations/{conversation}/messages", headers=family["owner"]).json()
    assert [(m["role"], m["content"][:22]) for m in messages] == [
        ("user", "quanto gastei em outub"),
        ("assistant", "Você gastou R$ 63,43 e"),
    ]
    conversations = client.get(f"/accounts/{family['id']}/conversations/", headers=family["owner"]).json()
    assert conversations[0]["title"] == "quanto gastei em outubro?"


def test_history_is_sent_to_planner_on_follow_up(client, llm, family):
    conversation = new_conversation(client, family)
    llm.plans += [QueryPlan(intent=Intent.SPENDING_SUMMARY), QueryPlan(intent=Intent.SPENDING_SUMMARY)]
    llm.answers += ["Total R$ 63,43 [D1].", "Mesmo total [D1]."]

    ask(client, family, conversation, "quanto gastei?")
    ask(client, family, conversation, "e no mês anterior?")

    assert "quanto gastei?" in llm.last_answer_prompt  # the history also reaches the answer writer


def test_out_of_scope_skips_tools_and_second_llm_call(client, llm, family):
    conversation = new_conversation(client, family)
    llm.plans.append(QueryPlan(intent=Intent.OUT_OF_SCOPE))

    answer = ask(client, family, conversation, "oi, tudo bem?").json()["assistant_message"]

    assert llm.calls == ["plan"]
    assert "Posso ajudar com suas despesas" in answer["content"]


def test_planner_invalid_output_falls_back_to_document_search(client, llm, family):
    conversation = new_conversation(client, family)
    llm.plans.append(LLMOutputError("garbage"))
    llm.answers.append("Não encontrei nos documentos.")

    response = ask(client, family, conversation, "o que diz meu contrato?")

    assert response.status_code == 201
    assert llm.calls == ["plan", "answer"]
    assert "busca nos documentos indisponível" in llm.last_answer_prompt


def test_llm_unavailable_returns_503_and_keeps_question(client, llm, family):
    conversation = new_conversation(client, family)
    llm.plans.append(LLMUnavailableError("all down", 60))

    response = ask(client, family, conversation, "quanto gastei?")

    assert response.status_code == 503
    messages = client.get(f"/accounts/{family['id']}/conversations/{conversation}/messages", headers=family["owner"]).json()
    assert [m["role"] for m in messages] == ["user"]


def test_conversations_are_private_between_members(client, llm, family):
    conversation = new_conversation(client, family, who="owner")

    assert client.get(f"/accounts/{family['id']}/conversations/", headers=family["member"]).json() == []
    assert ask(client, family, conversation, "quanto gastei?", who="member").status_code == 404
    url = f"/accounts/{family['id']}/conversations/{conversation}/messages"
    assert client.get(url, headers=family["member"]).status_code == 404


def test_outsider_cannot_use_account(client, llm, family):
    response = client.post(f"/accounts/{family['id']}/conversations/", json={}, headers=family["outsider"])

    assert response.status_code == 404


@pytest.mark.parametrize("content", ["", "x", "a" * 2001])
def test_question_validation(client, llm, family, content):
    conversation = new_conversation(client, family)

    assert ask(client, family, conversation, content).status_code == 422


def test_amounts_come_from_sql_not_llm(client, llm, family, session):
    """Even when the LLM 'makes things up', the facts sent to it come from the database."""
    conversation = new_conversation(client, family)
    llm.plans.append(QueryPlan(intent=Intent.SPENDING_SUMMARY))
    llm.answers.append("ok")

    ask(client, family, conversation, "quanto gastei no total?")

    assert '"total_gasto": "R$ 63,43"' in llm.last_answer_prompt
    assert str(Decimal("63.43")) not in llm.last_answer_prompt.replace("R$ 63,43", "")


@pytest.mark.parametrize(
    ("delay", "daily", "detail"),
    [
        (40_000, True, "AI daily limit reached"),
        (120, False, "Assistant temporarily unavailable, try again in a few minutes"),
    ],
    ids=["limite-diario", "momentaneo"],
)
def test_unavailable_ai_says_why_and_when(client, llm, family, delay, daily, detail):
    conversation_id = new_conversation(client, family)
    llm.plans.append(LLMUnavailableError("all models on cooldown", delay, daily_limit=daily))

    response = ask(client, family, conversation_id, "quanto gastei?")

    assert response.status_code == 503
    assert response.json() == {"detail": detail}
    assert response.headers["retry-after"] == str(delay)
