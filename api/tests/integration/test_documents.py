from datetime import datetime
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from core.exceptions import ServiceUnavailableError
from models import Document, IngestionJob, ProcessingStatus

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("dev_environment")]

PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
XML_BYTES = b'<?xml version="1.0" encoding="UTF-8"?><nfeProc versao="4.00"/>'


class FakeBlobStorage:
    def __init__(self) -> None:
        self.blobs: dict[str, dict] = {}
        self.fail_uploads = False

    def upload(self, key: str, data: BinaryIO, content_type: str, *, as_attachment: bool = False) -> str:
        if self.fail_uploads:
            raise ServiceUnavailableError("File storage unavailable")
        name = f"test/{key}"
        assert name not in self.blobs, "overwrite"
        self.blobs[name] = {"data": data.read(), "content_type": content_type, "attachment": as_attachment}
        return name

    def delete(self, blob_name: str) -> None:
        self.blobs.pop(blob_name, None)

    def generate_read_url(self, blob_name: str, expires_at: datetime) -> str:
        return f"https://fake.blob/{blob_name}?sig=x"


@pytest.fixture
def storage(client):
    from main import app
    from services.blob_storage import get_blob_storage

    fake = FakeBlobStorage()
    app.dependency_overrides[get_blob_storage] = lambda: fake
    return fake


@pytest.fixture
def family(client, make_user):
    owner_id, owner = make_user("doc-owner@example.com")
    member_id, member = make_user("doc-member@example.com")
    _, outsider = make_user("doc-outsider@example.com")

    account_id = client.post("/accounts/", json={"name": "Docs", "slug": "docs"}, headers=owner).json()["id"]
    client.post(f"/accounts/{account_id}/members/", json={"email": "doc-member@example.com"}, headers=owner)

    return {"account_id": account_id, "owner": owner, "member": member, "outsider": outsider, "member_id": member_id}


def upload(client, family, content=PDF_BYTES, filename="nota.pdf", who="member", content_type="application/pdf"):
    return client.post(
        f"/accounts/{family['account_id']}/documents/",
        files={"file": (filename, content, content_type)},
        headers=family[who],
    )


class TestUpload:
    def test_member_uploads_pdf_and_job_is_created(self, client, session, storage, family):
        response = upload(client, family)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["original_filename"] == "nota.pdf"
        assert body["mime_type"] == "application/pdf"
        assert body["file_size"] == len(PDF_BYTES)
        assert body["processing_status"] == "pending"
        assert body["uploaded_by"] == family["member_id"]

        document = session.get(Document, UUID(body["id"]))
        assert document.blob_path == f"test/expenses/{family['account_id']}/documents/{body['id']}.pdf"
        assert storage.blobs[document.blob_path]["data"] == PDF_BYTES

        jobs = session.scalars(select(IngestionJob).where(IngestionJob.document_id == document.id)).all()
        assert [(j.status, j.attempts) for j in jobs] == [(ProcessingStatus.PENDING, 0)]

    def test_type_detected_by_content_not_by_client(self, client, storage, family):
        response = upload(client, family, content=PNG_BYTES, filename="fake.pdf", content_type="application/pdf")

        assert response.status_code == 201
        assert response.json()["mime_type"] == "image/png"

    def test_xml_is_stored_as_attachment(self, client, storage, family):
        response = upload(client, family, content=XML_BYTES, filename="nfe.xml", content_type="text/xml")

        assert response.status_code == 201
        assert [b["attachment"] for b in storage.blobs.values()] == [True]

    def test_unsupported_type_is_rejected(self, client, storage, family):
        response = upload(client, family, content=b"MZ\x90\x00binary", filename="virus.pdf")

        assert response.status_code == 415
        assert storage.blobs == {}

    def test_empty_file_is_rejected(self, client, storage, family):
        response = upload(client, family, content=b"")

        assert response.status_code == 400
        assert storage.blobs == {}

    def test_file_too_large_is_rejected(self, client, storage, family, monkeypatch):
        from core.config import settings

        monkeypatch.setattr(settings.upload, "max_bytes", 32)

        response = upload(client, family)

        assert response.status_code == 413
        assert storage.blobs == {}

    def test_duplicate_file_is_conflict(self, client, storage, family):
        upload(client, family)

        response = upload(client, family, filename="outra-copia.pdf")

        assert response.status_code == 409
        assert len(storage.blobs) == 1

    def test_storage_failure_returns_503_and_persists_nothing(self, client, session, storage, family):
        storage.fail_uploads = True

        response = upload(client, family)

        assert response.status_code == 503
        assert response.json() == {"detail": "File storage unavailable"}
        assert session.scalars(select(Document)).all() == []

    def test_database_failure_removes_uploaded_blob(self, client, storage, family, monkeypatch):
        from repositories.document import IngestionJobRepository

        def boom(self, job):
            raise RuntimeError("db down")

        monkeypatch.setattr(IngestionJobRepository, "add", boom)

        with pytest.raises(RuntimeError, match="db down"):
            upload(client, family)

        assert storage.blobs == {}

    def test_outsider_cannot_upload(self, client, storage, family):
        assert upload(client, family, who="outsider").status_code == 404
        assert storage.blobs == {}

    def test_missing_file_is_unprocessable(self, client, storage, family):
        response = client.post(f"/accounts/{family['account_id']}/documents/", headers=family["owner"])

        assert response.status_code == 422


class TestReadDocuments:
    def test_list_get_and_download(self, client, storage, family):
        doc_id = upload(client, family).json()["id"]
        base = f"/accounts/{family['account_id']}/documents"

        listed = client.get(f"{base}/", headers=family["owner"])
        assert [d["id"] for d in listed.json()["items"]] == [doc_id]
        assert listed.json()["total"] == 1

        detail = client.get(f"{base}/{doc_id}", headers=family["owner"]).json()
        assert detail["id"] == doc_id
        # Not processed yet: nothing extracted; the internal error is never exposed.
        assert (detail["entries"], detail["items"]) == ([], [])
        assert "processing_error" not in detail

        download = client.get(f"{base}/{doc_id}/download", headers=family["member"])
        assert download.status_code == 200
        assert download.json()["url"].startswith("https://fake.blob/test/expenses/")
        assert download.json()["expires_at"]

    def test_pagination(self, client, storage, family):
        for i in range(3):
            upload(client, family, content=PDF_BYTES + bytes([i]), filename=f"{i}.pdf")
        base = f"/accounts/{family['account_id']}/documents/"

        first = client.get(base, params={"limit": 2}, headers=family["owner"]).json()["items"]
        rest_page = client.get(base, params={"limit": 2, "offset": 2}, headers=family["owner"]).json()
        rest = rest_page["items"]

        assert (len(first), len(rest), rest_page["total"]) == (2, 1, 3)
        pending = client.get(base, params={"status": "pending"}, headers=family["owner"]).json()
        failed = client.get(base, params={"status": "failed"}, headers=family["owner"]).json()
        assert (pending["total"], failed["total"]) == (3, 0)
        assert {d["id"] for d in first}.isdisjoint({d["id"] for d in rest})
        assert client.get(base, params={"limit": 101}, headers=family["owner"]).status_code == 422

    def test_outsider_cannot_read(self, client, storage, family):
        doc_id = upload(client, family).json()["id"]
        base = f"/accounts/{family['account_id']}/documents"

        for path in ("/", f"/{doc_id}", f"/{doc_id}/download"):
            assert client.get(f"{base}{path}", headers=family["outsider"]).status_code == 404

    def test_document_from_other_account_is_not_found(self, client, storage, family, make_user):
        doc_id = upload(client, family).json()["id"]
        _, other = make_user("other-owner@example.com")
        other_account = client.post("/accounts/", json={"name": "O", "slug": "outra"}, headers=other).json()["id"]

        response = client.get(f"/accounts/{other_account}/documents/{doc_id}/download", headers=other)

        assert response.status_code == 404

    def test_unknown_document_is_not_found(self, client, storage, family):
        response = client.get(f"/accounts/{family['account_id']}/documents/{uuid4()}", headers=family["owner"])

        assert response.status_code == 404


class FakeVectorStore:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def delete_document(self, account_id, document_id) -> None:
        self.deleted.append((str(account_id), str(document_id)))


@pytest.fixture
def vectors(client):
    from main import app
    from services.search import get_vector_store

    fake = FakeVectorStore()
    app.dependency_overrides[get_vector_store] = lambda: fake
    return fake


def doc_url(family, doc_id, suffix=""):
    return f"/accounts/{family['account_id']}/documents/{doc_id}{suffix}"


class TestDeleteDocument:
    def test_uploader_deletes_everything(self, client, storage, vectors, family, session):
        from models import DocumentItem, FinancialEntry, IngestionJob

        doc_id = upload(client, family).json()["id"]
        blob = next(iter(storage.blobs))

        response = client.delete(doc_url(family, doc_id), headers=family["member"])

        assert response.status_code == 204
        assert client.get(doc_url(family, doc_id), headers=family["member"]).status_code == 404
        assert blob not in storage.blobs
        assert vectors.deleted == [(family["account_id"], doc_id)]
        for model in (IngestionJob, FinancialEntry, DocumentItem):
            assert session.query(model).filter_by(document_id=doc_id).count() == 0

    def test_owner_can_delete_member_upload(self, client, storage, vectors, family):
        doc_id = upload(client, family, who="member").json()["id"]

        assert client.delete(doc_url(family, doc_id), headers=family["owner"]).status_code == 204

    def test_member_cannot_delete_someone_elses_upload(self, client, storage, vectors, family):
        doc_id = upload(client, family, who="owner").json()["id"]

        response = client.delete(doc_url(family, doc_id), headers=family["member"])

        assert response.status_code == 403
        assert client.get(doc_url(family, doc_id), headers=family["owner"]).status_code == 200

    def test_outsider_gets_404(self, client, storage, vectors, family):
        doc_id = upload(client, family).json()["id"]

        assert client.delete(doc_url(family, doc_id), headers=family["outsider"]).status_code == 404

    def test_cannot_delete_while_processing(self, client, storage, vectors, family, session):
        from models import Document, ProcessingStatus

        doc_id = upload(client, family).json()["id"]
        session.get(Document, doc_id).processing_status = ProcessingStatus.PROCESSING
        session.flush()

        response = client.delete(doc_url(family, doc_id), headers=family["member"])

        assert response.status_code == 409
        assert vectors.deleted == []


class TestReprocessDocument:
    def test_reprocess_requeues_failed_document(self, client, storage, family, session):
        from models import Document, IngestionJob, ProcessingStatus

        doc_id = upload(client, family).json()["id"]
        job = session.query(IngestionJob).filter_by(document_id=doc_id).one()
        job.status = ProcessingStatus.FAILED
        session.get(Document, doc_id).processing_status = ProcessingStatus.FAILED
        session.flush()

        response = client.post(doc_url(family, doc_id, "/reprocess"), headers=family["member"])

        assert response.status_code == 202
        assert response.json()["processing_status"] == "pending"
        assert session.query(IngestionJob).filter_by(document_id=doc_id, status=ProcessingStatus.PENDING).count() == 1

    def test_already_queued_is_conflict(self, client, storage, family):
        doc_id = upload(client, family).json()["id"]  # the upload already enqueues

        response = client.post(doc_url(family, doc_id, "/reprocess"), headers=family["member"])

        assert response.status_code == 409

    def test_outsider_gets_404(self, client, storage, family):
        doc_id = upload(client, family).json()["id"]

        assert client.post(doc_url(family, doc_id, "/reprocess"), headers=family["outsider"]).status_code == 404


def test_deferred_extraction_shows_next_attempt(client, storage, family, session):
    from datetime import UTC, datetime, timedelta

    from models import IngestionJob

    doc_id = upload(client, family).json()["id"]
    later = datetime.now(UTC) + timedelta(hours=5)
    session.query(IngestionJob).filter_by(document_id=doc_id).one().available_at = later
    session.flush()

    listed = client.get(f"/accounts/{family['account_id']}/documents/", headers=family["owner"]).json()["items"][0]
    detail = client.get(doc_url(family, doc_id), headers=family["owner"]).json()

    assert listed["next_attempt_at"] is not None
    assert detail["next_attempt_at"] == listed["next_attempt_at"]
