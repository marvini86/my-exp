"""VectorStore against a real Qdrant, in a temporary collection (deleted at the end).

Requires TEST_QDRANT=1 (uses QDRANT_URL/QDRANT_API_KEY from settings). No
existing collection is touched.
"""

import os
import uuid

import pytest
from qdrant_client import QdrantClient

from core.config import settings
from repositories.vector_store import Chunk, VectorStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("TEST_QDRANT") != "1", reason="TEST_QDRANT=1 não definido"),
]

DIM = 4
ACCOUNT_A, ACCOUNT_B = uuid.uuid4(), uuid.uuid4()


def vec(*values: float) -> list[float]:
    return list(values) + [0.0] * (DIM - len(values))


@pytest.fixture
def store():
    client = QdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None, timeout=10)
    store = VectorStore(client, f"test_expense_{uuid.uuid4().hex[:10]}")
    store.ensure_collection({"m1": DIM, "m2": DIM})
    yield store
    client.delete_collection(store.collection)
    client.close()


def index(store, account, document_id, contents: list[str], vectors: list[list[float]], document_type="nfce"):
    chunks = [Chunk(document_id, i, text, "summary", document_type) for i, text in enumerate(contents)]
    return store.replace_document(account, document_id, chunks, {"m1": vectors, "m2": vectors})


def test_collection_has_named_vectors_and_tenant_index(store):
    info = store.client.get_collection(store.collection)

    assert set(info.config.params.vectors) == {"m1", "m2"}
    assert info.payload_schema["account_id"].params.is_tenant is True


def test_search_never_crosses_accounts(store):
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    index(store, ACCOUNT_A, doc_a, ["leite da conta A"], [vec(1, 0)])
    index(store, ACCOUNT_B, doc_b, ["leite da conta B"], [vec(1, 0)])  # identical vector on purpose

    hits_a = store.search(ACCOUNT_A, "m1", vec(1, 0), limit=10)
    hits_b = store.search(ACCOUNT_B, "m2", vec(1, 0), limit=10)

    assert [h.document_id for h in hits_a] == [doc_a]
    assert [h.document_id for h in hits_b] == [doc_b]
    assert store.search(uuid.uuid4(), "m1", vec(1, 0), limit=10) == []


def test_results_ordered_by_similarity(store):
    doc = uuid.uuid4()
    index(store, ACCOUNT_A, doc, ["energia", "mercado"], [vec(1, 0), vec(0, 1)])

    hits = store.search(ACCOUNT_A, "m1", vec(0.1, 0.9), limit=2)

    assert [h.content for h in hits] == ["mercado", "energia"]
    assert hits[0].score > hits[1].score


def test_document_type_filter(store):
    index(store, ACCOUNT_A, uuid.uuid4(), ["conta"], [vec(1, 0)], document_type="energy_bill")
    index(store, ACCOUNT_A, uuid.uuid4(), ["cupom"], [vec(1, 0)], document_type="nfce")

    hits = store.search(ACCOUNT_A, "m1", vec(1, 0), limit=10, document_type="energy_bill")

    assert [h.content for h in hits] == ["conta"]


def test_reindex_replaces_previous_points(store):
    doc = uuid.uuid4()
    index(store, ACCOUNT_A, doc, ["v1 a", "v1 b", "v1 c"], [vec(1, 0)] * 3)
    index(store, ACCOUNT_A, doc, ["v2"], [vec(1, 0)])

    assert [h.content for h in store.search(ACCOUNT_A, "m1", vec(1, 0), limit=10)] == ["v2"]


def test_delete_is_scoped_to_account(store):
    doc = uuid.uuid4()
    index(store, ACCOUNT_A, doc, ["dado da A"], [vec(1, 0)])

    store.delete_document(ACCOUNT_B, doc)  # another account: removes nothing
    assert len(store.search(ACCOUNT_A, "m1", vec(1, 0), limit=10)) == 1

    store.delete_document(ACCOUNT_A, doc)
    assert store.search(ACCOUNT_A, "m1", vec(1, 0), limit=10) == []


def test_vectors_for_unknown_models_are_ignored(store):
    doc = uuid.uuid4()
    chunks = [Chunk(doc, 0, "x", "summary")]

    stored = store.replace_document(ACCOUNT_A, doc, chunks, {"m1": [vec(1, 0)], "not_in_collection": [vec(1)]})

    assert stored == 1
    assert len(store.search(ACCOUNT_A, "m1", vec(1, 0))) == 1
