"""Full flow, as the frontend uses it:

Google login → account → invitation → upload (member) → worker extracts and indexes
→ question to the assistant with a citation → download → logout.

Unlike the other API tests, here the data is COMMITTED (the API uses
the myexp_app role and the worker myexp_worker, on separate connections, as in
production). At the end, TRUNCATE. Use a disposable database only.
Simulated externals: Google (local RSA key), Azure Blob, LLM and embeddings.
"""

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core.config import WorkerSettings
from google_tokens import make_id_token, make_verifier
from services.assistant.planner import Intent, QueryPlan
from services.extraction.nfe_xml import NFeXmlExtractor
from services.extraction.processor import ExtractionProcessor
from services.ingestion import IngestionService
from test_assistant_api import FakeLLMRouter, NoSearch

APP_URL = os.getenv("TEST_DATABASE_URL")
ADMIN_URL = os.getenv("TEST_ADMIN_DATABASE_URL")
WORKER_URL = os.getenv("TEST_WORKER_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (APP_URL and ADMIN_URL and WORKER_URL), reason="URLs de teste do banco não definidas"),
    pytest.mark.usefixtures("production_environment"),
    # Real access rule: the owner signs in as admin; the invitee through the invitation.
    pytest.mark.real_access,
]

NFCE = (Path(__file__).resolve().parents[1] / "fixtures" / "nfce_supermercado.xml").read_bytes()


class FakeStorage:
    """In-memory Azure Blob, shared between the API (upload) and the worker (download)."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def upload(self, key, data, content_type, *, as_attachment=False) -> str:
        self.blobs[key] = data.read()
        return key

    def download(self, blob_name: str) -> bytes:
        return self.blobs[blob_name]

    def delete(self, blob_name: str) -> None:
        self.blobs.pop(blob_name, None)

    def generate_read_url(self, blob_name: str, expires_at: datetime) -> str:
        return f"https://fake.blob/{blob_name}?sig=x"


class MarkIndexed:
    """Simulated indexing (Qdrant/embeddings have their own tests)."""

    def process(self, session, document, content) -> None:
        document.indexed_at = datetime.now(UTC)


@pytest.fixture
def env(monkeypatch):
    from database.connection import get_db
    from main import app
    from routes.conversation import get_llm_router
    from services.blob_storage import get_blob_storage
    from services.google_identity import get_google_verifier
    from services.search import get_embedding_router, get_vector_store

    app_engine, worker_engine, admin_engine = (create_engine(url) for url in (APP_URL, WORKER_URL, ADMIN_URL))
    app_sessions = sessionmaker(bind=app_engine, expire_on_commit=False)

    def real_db():
        with app_sessions() as session:
            yield session

    storage, llm = FakeStorage(), FakeLLMRouter()
    app.dependency_overrides.update({
        get_db: real_db,
        get_google_verifier: lambda: make_verifier(),
        get_blob_storage: lambda: storage,
        get_llm_router: lambda: llm,
        get_embedding_router: lambda: NoSearch(),
        get_vector_store: lambda: None,
    })
    worker = IngestionService(
        session_factory=sessionmaker(bind=worker_engine, expire_on_commit=False),
        storage=storage,
        processor=ExtractionProcessor([NFeXmlExtractor()]),
        worker_id="flow-test",
        config=WorkerSettings(),
        index_processor=MarkIndexed(),
    )
    try:
        yield TestClient(app), worker, llm
    finally:
        app.dependency_overrides.clear()
        with admin_engine.begin() as conn:
            conn.execute(text(
                "TRUNCATE users, user_sessions, accounts, account_members, categories, documents, ingestion_jobs, "
                "financial_entries, document_items, conversations, messages CASCADE"
            ))
        for engine in (app_engine, worker_engine, admin_engine):
            engine.dispose()


def bearer(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_family_flow_from_login_to_cited_answer(env, monkeypatch):
    from core.config import settings

    client, worker, llm = env
    suffix = uuid4().hex[:6]
    monkeypatch.setattr(settings.auth, "admin_emails", [f"o-{suffix}@gmail.com"])

    # 1. Owner login and creation of the family account.
    owner = client.post("/auth/google", json={"id_token": make_id_token(sub=f"o-{suffix}", email=f"o-{suffix}@gmail.com")})
    assert owner.status_code == 200, owner.text
    owner = owner.json()
    account = client.post("/accounts/", json={"name": "Família", "slug": f"familia-{suffix}"}, headers=bearer(owner))
    assert account.status_code == 201, account.text
    base = f"/accounts/{account.json()['id']}"

    # 2. Invitation by e-mail; the invitee signs in with Google and is already a member.
    invite = client.post(f"{base}/members/", json={"email": f"b-{suffix}@gmail.com"}, headers=bearer(owner))
    assert invite.status_code == 201 and invite.json()["pending"] is True
    member = client.post(
        "/auth/google", json={"id_token": make_id_token(sub=f"b-{suffix}", email=f"b-{suffix}@gmail.com", name="Bia")}
    ).json()
    assert member["user"]["id"] == invite.json()["user_id"]
    # The invitee uses the account but does not invite anyone.
    assert client.post(f"{base}/members/", json={"email": f"x-{suffix}@gmail.com"}, headers=bearer(member)).status_code == 403

    # 3. A convidada envia o cupom fiscal.
    upload = client.post(
        f"{base}/documents/", files={"file": ("cupom.xml", NFCE, "application/xml")}, headers=bearer(member)
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["id"]
    assert upload.json()["processing_status"] == "pending"

    # 4. Worker: extraction (ingest) and then indexing (index).
    processed = 0
    while worker.process_next():
        processed += 1
    assert processed == 2

    document = client.get(f"{base}/documents/{document_id}", headers=bearer(owner)).json()
    assert document["processing_status"] == "completed"
    assert document["document_type"] == "nfce"
    assert document["indexed_at"] is not None
    # Category assigned automatically (no manual registration).
    assert document["entries"][0]["category"]["name"] == "Mercado"

    # 5. The owner asks; values come from SQL over what was extracted.
    llm.plans.append(QueryPlan(intent=Intent.PRICE_HISTORY, product="leite"))
    llm.answers.append("O leite integral saiu mais barato no cupom enviado [D1].")
    conversation = client.post(f"{base}/conversations/", json={}, headers=bearer(owner)).json()
    answer = client.post(
        f"{base}/conversations/{conversation['id']}/messages",
        json={"content": "Onde comprei leite mais barato?"},
        headers=bearer(owner),
    )
    assert answer.status_code == 201, answer.text
    citation = answer.json()["assistant_message"]["citations"][0]
    assert citation["document_id"] == document_id
    assert "LEITE INTEGRAL" in llm.last_answer_prompt

    # 6. The citation leads to the original file.
    download = client.get(citation["download_path"], headers=bearer(owner))
    assert download.status_code == 200, download.text

    # 7. Conversations are private; logout ends access.
    assert client.get(f"{base}/conversations/{conversation['id']}/messages", headers=bearer(member)).status_code == 404
    assert client.post("/auth/logout", headers=bearer(owner)).status_code == 204
    assert client.get(f"{base}/documents/", headers=bearer(owner)).status_code == 401
