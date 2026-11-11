from uuid import UUID, uuid4

import pytest

from core.exceptions import ServiceUnavailableError
from models import Document
from repositories.vector_store import SearchHit
from services.embeddings import EmbeddingUnavailableError

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("dev_environment")]


class FakeRouter:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    def embed_query(self, text):
        self.queries.append(text)
        if self.error:
            raise self.error
        return "m1", [0.1, 0.2]


class FakeStore:
    """Simulates Qdrant: stores hits per account and records each search's account_id."""

    def __init__(self) -> None:
        self.hits: dict[UUID, list[SearchHit]] = {}
        self.searched_accounts: list[UUID] = []
        self.error: Exception | None = None

    def search(self, account_id, vector_name, vector, *, limit, document_type=None):
        self.searched_accounts.append(account_id)
        if self.error:
            raise self.error
        hits = [h for h in self.hits.get(account_id, []) if document_type in (None, h.document_type)]
        return hits[:limit]


@pytest.fixture
def fakes(client):
    from main import app
    from services.search import get_embedding_router, get_vector_store

    router, store = FakeRouter(), FakeStore()
    app.dependency_overrides[get_embedding_router] = lambda: router
    app.dependency_overrides[get_vector_store] = lambda: store
    return router, store


@pytest.fixture
def account(client, make_user, session):
    _, owner = make_user("search-owner@example.com")
    _, outsider = make_user("search-outsider@example.com")
    account_id = client.post("/accounts/", json={"name": "Busca", "slug": "busca"}, headers=owner).json()["id"]

    document = Document(
        account_id=UUID(account_id),
        original_filename="conta-luz.pdf",
        mime_type="application/pdf",
        blob_path=f"test/{uuid4()}.pdf",
        document_type="energy_bill",
    )
    from database.tenant_context import set_account_context

    set_account_context(session, UUID(account_id), UUID(owner["X-Dev-User-Id"]))
    session.add(document)
    session.flush()

    return {"id": account_id, "owner": owner, "outsider": outsider, "document": document}


def hit(document_id, content="Conta de energia: R$ 217,80", score=0.9, document_type="energy_bill"):
    return SearchHit(document_id=document_id, chunk_index=0, content=content, kind="summary", document_type=document_type, score=score)


def search(client, account, q="quanto paguei de luz?", who="owner", **params):
    return client.get(f"/accounts/{account['id']}/search/", params={"q": q, **params}, headers=account[who])


def test_returns_hits_with_document_reference(client, fakes, account):
    router, store = fakes
    store.hits[UUID(account["id"])] = [hit(account["document"].id)]

    response = search(client, account)

    assert response.status_code == 200
    body = response.json()
    assert (body["query"], body["model"]) == ("quanto paguei de luz?", "m1")
    (result,) = body["results"]
    assert result["original_filename"] == "conta-luz.pdf"
    assert result["content"] == "Conta de energia: R$ 217,80"
    assert result["download_path"] == f"/accounts/{account['id']}/documents/{account['document'].id}/download"


def test_search_is_always_scoped_to_the_account(client, fakes, account):
    _, store = fakes

    search(client, account)

    assert store.searched_accounts == [UUID(account["id"])]


def test_hits_for_missing_documents_are_dropped(client, fakes, account):
    _, store = fakes
    store.hits[UUID(account["id"])] = [hit(uuid4()), hit(account["document"].id, score=0.5)]

    results = search(client, account).json()["results"]

    assert [r["document_id"] for r in results] == [str(account["document"].id)]


def test_document_type_filter_is_forwarded(client, fakes, account):
    _, store = fakes
    store.hits[UUID(account["id"])] = [hit(account["document"].id)]

    assert search(client, account, document_type="nfce").json()["results"] == []
    assert len(search(client, account, document_type="energy_bill").json()["results"]) == 1


def test_outsider_gets_404_and_store_is_not_queried(client, fakes, account):
    _, store = fakes

    assert search(client, account, who="outsider").status_code == 404
    assert store.searched_accounts == []


def test_embeddings_unavailable_returns_503(client, fakes, account):
    router, _ = fakes
    router.error = EmbeddingUnavailableError("all down")

    response = search(client, account)

    assert response.status_code == 503
    assert response.json() == {"detail": "Semantic search temporarily unavailable"}


def test_vector_store_unavailable_returns_503(client, fakes, account):
    _, store = fakes
    store.error = ServiceUnavailableError("Search index unavailable")

    assert search(client, account).status_code == 503


@pytest.mark.parametrize("params", [{"q": "x"}, {"q": "ok", "limit": 0}, {"q": "ok", "limit": 21}])
def test_invalid_query_params(client, fakes, account, params):
    response = client.get(f"/accounts/{account['id']}/search/", params=params, headers=account["owner"])

    assert response.status_code == 422


def test_requires_authentication(client, fakes, account):
    assert client.get(f"/accounts/{account['id']}/search/", params={"q": "luz"}).status_code == 401
