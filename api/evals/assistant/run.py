"""Assistant evaluation with the real AI, over the fixed set in dataset.py.

Usage (from api/, with the Docker test Postgres running and the migrations applied):
    EVAL_DATABASE_URL=postgresql+psycopg://myexp_app:...@127.0.0.1:55432/myexp_test \\
        .venv/Scripts/python.exe evals/assistant/run.py [--only id1,id2] [--tag produto] [--pause 3]

- Everything runs in a transaction rolled back at the end: the test database keeps nothing.
- The documents are indexed in a dedicated Qdrant collection ("myexp_eval"), cleaned at the end.
- Uses quota: ~2 text calls per question + embeddings for the documents and the searches.
- Detailed result in evals/results/assistant-YYYYMMDD-HHMM.json (outside Git).
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

API_DIR = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(API_DIR / "app"), str(API_DIR)]

import logging  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from qdrant_client import QdrantClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from core.account_context import AccountContext  # noqa: E402
from core.auth import DEV_USER_HEADER  # noqa: E402
from core.config import settings  # noqa: E402
from core.exceptions import ServiceUnavailableError  # noqa: E402
from database.connection import get_db  # noqa: E402
from database.tenant_context import set_account_context  # noqa: E402
from evals.assistant.cases import CASES, Case, Turn  # noqa: E402
from evals.assistant.dataset import DOCUMENTS, EVAL_TODAY  # noqa: E402
from models import Document  # noqa: E402
from models.account_member import AccountRole  # noqa: E402
from models.conversation import Message  # noqa: E402
from models.document import ProcessingStatus  # noqa: E402
from models.user import User  # noqa: E402
from repositories.conversation import ConversationRepository  # noqa: E402
from repositories.document import DocumentRepository  # noqa: E402
from repositories.financial_queries import FinancialQueryRepository  # noqa: E402
from repositories.platform_access import PlatformAccessRepository  # noqa: E402
from repositories.vector_store import VectorStore  # noqa: E402
from schemas.conversation import CreateConversationRequest  # noqa: E402
from services.assistant.items import ItemResolver  # noqa: E402
from services.assistant.service import AssistantService  # noqa: E402
from services.assistant.tools import AssistantTools  # noqa: E402
from services.extraction.processor import ExtractionProcessor  # noqa: E402
from services.indexing import IndexingProcessor  # noqa: E402
from services.llm_router import LLMRouter  # noqa: E402
from services.search import SearchService, get_embedding_router  # noqa: E402

EVAL_COLLECTION = "myexp_eval"
RESULTS_DIR = API_DIR / "evals" / "results"


# --- answer checks ------------------------------------------------------------


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", text.replace(" ", " "))


def _found(expected: str, answer: str) -> bool:
    wanted = normalize(expected)
    if wanted in answer:
        return True
    if re.fullmatch(r"[\d.]+,\d{2}", wanted) and wanted.replace(".", "").replace(",", ".") in answer:
        return True  # "369,12" escrito como "369.12"
    digits = re.sub(r"\D", "", expected)
    return len(digits) >= 8 and digits in re.sub(r"\D", "", answer)  # CNPJ with or without punctuation


def check(turn: Turn, answer: str, intent: str | None, citations: int) -> list[str]:
    text = normalize(answer)
    problems = [f"missing '{e}'" for e in turn.include if not _found(e, text)]
    if turn.include_any and not any(_found(e, text) for e in turn.include_any):
        problems.append(f"missing one of {list(turn.include_any)}")
    problems += [f"should not contain '{e}'" for e in turn.exclude if _found(e, text)]
    if citations < turn.citations:
        problems.append(f"{citations} source(s) cited, expected ≥ {turn.citations}")
    return problems


# --- dataset setup -----------------------------------------------------------------------


class FixedExtractor:
    """Replaces the vision AI: returns the verified reading of the document."""

    def __init__(self, extracted) -> None:
        self.extracted = extracted

    def supports(self, mime_type: str) -> bool:
        return True

    def extract(self, content: bytes, mime_type: str):
        return self.extracted


def create_account(session: Session) -> tuple[UUID, UUID]:
    from main import app

    settings.environment = "development"
    PlatformAccessRepository.is_listed = lambda self, email: True
    app.dependency_overrides[get_db] = lambda: session
    client = TestClient(app)
    user_id = client.post("/users/", json={"name": "Eval", "email": f"eval-{uuid4().hex[:8]}@example.com"}).json()["id"]
    slug = f"eval-{uuid4().hex[:8]}"
    response = client.post("/accounts/", json={"name": "Família Prova", "slug": slug}, headers={DEV_USER_HEADER: user_id})
    assert response.status_code == 201, response.text
    app.dependency_overrides.clear()
    return UUID(response.json()["id"]), UUID(user_id)


def seed(session: Session, account_id: UUID, user_id: UUID, store: VectorStore) -> list[UUID]:
    set_account_context(session, account_id, user_id)
    indexer = IndexingProcessor(get_embedding_router(), store)
    ids = []
    for doc in DOCUMENTS:
        document = Document(
            account_id=account_id,
            uploaded_by=user_id,
            original_filename=doc.filename,
            mime_type="image/jpeg",
            blob_path=f"eval/{uuid4()}",
            processing_status=ProcessingStatus.COMPLETED,
            created_at=doc.uploaded_at,
        )
        session.add(document)
        session.flush()
        ExtractionProcessor([FixedExtractor(doc.extracted)]).process(session, document, b"")
        indexer.process(session, document, b"")
        ids.append(document.id)
    session.commit()
    return ids


# --- execution --------------------------------------------------------------------------------


def run_case(service: AssistantService, session: Session, context: AccountContext, case: Case, pause: float) -> dict:
    conversation = service.create_conversation(context, CreateConversationRequest(title=case.id))
    turns = []
    for turn in case.turns:
        started = time.monotonic()
        try:
            response = service.ask(context, conversation.id, turn.question)
        except ServiceUnavailableError as exc:
            turns.append({"question": turn.question, "error": str(exc), "problems": ["AI unavailable"]})
            break
        elapsed = time.monotonic() - started
        message = session.get(Message, response.assistant_message.id)
        metadata = message.metadata_ or {}
        plan = metadata.get("plan") or {}
        answer = response.assistant_message.content
        problems = check(turn, answer, plan.get("intent"), len(response.assistant_message.citations))
        turns.append(
            {
                "question": turn.question,
                "answer": answer,
                "problems": problems,
                "intent": plan.get("intent"),
                "expected_intent": turn.intent,
                "plan": plan,
                "seconds": round(elapsed, 1),
                "models": [metadata.get("planner_model"), metadata.get("answer_model")],
            }
        )
        time.sleep(pause)
    return {"id": case.id, "tags": list(case.tags), "passed": all(not t["problems"] for t in turns), "turns": turns}


def main() -> None:
    parser = argparse.ArgumentParser(description="Assistant evaluation")
    parser.add_argument("--only", help="comma-separated ids")
    parser.add_argument("--tag", help="only cases with this tag")
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between questions (AI per-minute limit)")
    args = parser.parse_args()

    url = os.environ.get("EVAL_DATABASE_URL")
    if not url:
        sys.exit("Set EVAL_DATABASE_URL (a DISPOSABLE local Postgres with the migrations applied).")
    if urlparse(url.replace("+psycopg", "")).hostname not in ("127.0.0.1", "localhost"):
        sys.exit("EVAL_DATABASE_URL must be a disposable local database (never a server's).")

    logging.basicConfig(level=logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8")
    cases = [c for c in CASES if (not args.only or c.id in args.only.split(",")) and (not args.tag or args.tag in c.tags)]

    qdrant = QdrantClient(url=settings.qdrant.url, api_key=settings.qdrant.api_key or None, timeout=30)
    store = VectorStore(qdrant, EVAL_COLLECTION)
    engine = create_engine(url)
    results: list[dict] = []
    started = time.monotonic()

    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        account_id, user_id = create_account(session)
        document_ids: list[UUID] = []
        try:
            print(f"Building the dataset: {len(DOCUMENTS)} documents (real processor + embeddings)...")
            document_ids = seed(session, account_id, user_id, store)
            user = session.get(User, user_id)
            context = AccountContext(account_id=account_id, user=user, role=AccountRole.OWNER)
            llm = LLMRouter.for_purpose(settings, vision=False)
            tools = AssistantTools(
                FinancialQueryRepository(session),
                SearchService(DocumentRepository(session), get_embedding_router(), store),
                ItemResolver(llm),
            )
            service = AssistantService(session, ConversationRepository(session), tools, llm, today=lambda: EVAL_TODAY)
            print(f"Running {len(cases)} cases (today = {EVAL_TODAY:%Y-%m-%d})...\n")
            for case in cases:
                set_account_context(session, account_id, user_id)
                result = run_case(service, session, context, case, args.pause)
                results.append(result)
                mark = "OK " if result["passed"] else "ERR"
                seconds = sum(t.get("seconds", 0) for t in result["turns"])
                print(f"[{mark}] {case.id:22} {seconds:5.1f}s")
                for t in result["turns"]:
                    if t["problems"]:
                        intent = f" | intent {t.get('intent')}" + (f" (expected {t['expected_intent']})" if t.get("expected_intent") and t.get("expected_intent") != t.get("intent") else "")
                        print(f"      P: {t['question']}{intent}")
                        print(f"      ✗ {'; '.join(t['problems'])}")
                        print(f"      R: {(t.get('answer') or t.get('error') or '')[:300].replace(chr(10), ' ')}")
                if any("AI unavailable" in t["problems"] for t in result["turns"]):
                    print("\nAI unavailable (limit?): stopping.")
                    break
        finally:
            for document_id in document_ids:
                store.delete_document(account_id, document_id)
            session.close()
            transaction.rollback()
    engine.dispose()

    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} cases passed in {time.monotonic() - started:.0f}s")
    by_tag: dict[str, list[bool]] = {}
    for r in results:
        for tag in r["tags"]:
            by_tag.setdefault(tag, []).append(r["passed"])
    print("By topic: " + ", ".join(f"{t} {sum(v)}/{len(v)}" for t, v in sorted(by_tag.items())))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"assistant-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(
        json.dumps({"today": EVAL_TODAY.isoformat(), "passed": passed, "total": len(results), "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Details: {out}")


if __name__ == "__main__":
    main()
