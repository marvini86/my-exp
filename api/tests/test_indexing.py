from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pdf_factory import ENERGY_BILL_LINES, make_pdf
from services.document_processor import DeferProcessingError
from services.embeddings import EmbeddingUnavailableError
from services.indexing import IndexingProcessor, build_chunks, build_summary_chunks, format_brl


def nfce_document(items: int = 3, **overrides) -> SimpleNamespace:
    result = {
        "document_type": "nfce",
        "description": "Mercado Exemplo - NFC-e 12345",
        "establishment": "Mercado Exemplo",
        "establishment_tax_id": "12345678000190",
        "issued_at": "2024-01-10",
        "paid_at": "2024-01-10T18:32:15-03:00",
        "payment_status": "paid",
        "total_amount": "63.43",
        "items": [
            {"description": f"PRODUTO {i}", "quantity": "2.000", "unit": "UN", "unit_price": "4.8900", "total_price": "9.78"}
            for i in range(items)
        ],
        "metadata": {"source": "nfe_xml", "number": "12345", "payment_methods": ["pix"]},
    }
    fields = dict(
        id=uuid4(),
        account_id=uuid4(),
        original_filename="cupom.xml",
        mime_type="application/xml",
        document_type="nfce",
        extracted_data={"status": "extracted", "result": result},
        indexed_at=None,
    )
    return SimpleNamespace(**(fields | overrides))


class TestFormatting:
    @pytest.mark.parametrize(("value", "expected"), [("1234.5", "R$ 1.234,50"), ("63.43", "R$ 63,43"), (None, None), ("abc", None)])
    def test_format_brl(self, value, expected):
        assert format_brl(value) == expected


class TestSummaryChunks:
    def test_summary_is_readable_portuguese(self):
        (chunk,) = build_summary_chunks(nfce_document(), chunk_size=1000)

        assert "Cupom fiscal (NFC-e): Mercado Exemplo - NFC-e 12345" in chunk
        assert "Emissor: Mercado Exemplo (CNPJ/CPF 12345678000190)" in chunk
        assert "Emissão: 10/01/2024" in chunk
        assert "Valor total: R$ 63,43 (pago)" in chunk
        assert "- PRODUTO 0 | qtd 2 UN | unitário R$ 4,89 | total R$ 9,78" in chunk
        assert "nfe_xml" not in chunk  # technical metadata is left out

    def test_many_items_split_with_header_repeated(self):
        chunks = build_summary_chunks(nfce_document(items=60), chunk_size=600)

        assert len(chunks) > 1
        assert all(chunk.startswith("Cupom fiscal (NFC-e): Mercado Exemplo") for chunk in chunks)
        assert all("Emissor: Mercado Exemplo" in chunk for chunk in chunks)
        assert "Itens (continuação):" in chunks[1]
        joined = "\n".join(chunks)
        assert all(f"- PRODUTO {i} " in joined for i in range(60))

    def test_document_without_extraction_has_no_summary(self):
        assert build_summary_chunks(nfce_document(extracted_data={"status": "skipped"}), chunk_size=1000) == []


class TestBuildChunks:
    def test_pdf_adds_text_chunks(self):
        document = nfce_document(mime_type="application/pdf", original_filename="conta.pdf")

        chunks = build_chunks(document, make_pdf(ENERGY_BILL_LINES), chunk_size=300, chunk_overlap=50)

        kinds = [c.kind for c in chunks]
        assert kinds[0] == "summary" and "text" in kinds
        assert [c.index for c in chunks] == list(range(len(chunks)))
        assert any("TOTAL A PAGAR" in c.content for c in chunks if c.kind == "text")
        assert all(c.content.startswith("Texto do documento conta.pdf") for c in chunks if c.kind == "text")

    def test_xml_only_summary(self):
        chunks = build_chunks(nfce_document(), b"<xml/>", chunk_size=1000, chunk_overlap=100)

        assert [c.kind for c in chunks] == ["summary"]


class FakeRouter:
    def __init__(self, error=None):
        self.error = error

    def embed_documents(self, texts):
        if self.error:
            raise self.error
        return {"m1": [[0.1, 0.2] for _ in texts]}


class FakeStore:
    def __init__(self):
        self.ensured = []
        self.replaced = []

    def ensure_collection(self, dimensions):
        self.ensured.append(dimensions)

    def replace_document(self, account_id, document_id, chunks, vectors):
        self.replaced.append((account_id, document_id, len(chunks), set(vectors)))
        return len(chunks)


class TestIndexingProcessor:
    def test_indexes_and_marks_document(self):
        store = FakeStore()
        processor = IndexingProcessor(FakeRouter(), store)
        document = nfce_document()

        processor.process(None, document, b"<xml/>")
        processor.process(None, nfce_document(), b"<xml/>")

        assert store.ensured == [{"m1": 2}]  # collection ensured only once
        assert store.replaced[0] == (document.account_id, document.id, 1, {"m1"})
        assert isinstance(document.indexed_at, datetime) and document.indexed_at.tzinfo is UTC

    def test_embeddings_unavailable_defers(self):
        processor = IndexingProcessor(FakeRouter(EmbeddingUnavailableError("all down", cooldown_seconds=120)), FakeStore())
        document = nfce_document()

        with pytest.raises(DeferProcessingError) as raised:
            processor.process(None, document, b"<xml/>")

        assert raised.value.delay_seconds == 120
        assert document.indexed_at is None

    def test_nothing_to_index_still_marks_indexed(self):
        store = FakeStore()
        document = nfce_document(extracted_data={"status": "skipped"})

        IndexingProcessor(FakeRouter(), store).process(None, document, b"<xml/>")

        assert store.replaced == [] and document.indexed_at is not None
