"""Document indexing for semantic search (the worker's `index` job).

Each document becomes chunks:
- a structured summary built from the extraction (type, issuer, dates,
  total, items), with the header repeated in each part;
- the PDF text, when there is one.

The chunks are embedded by every available embedding model and
stored in Qdrant with the document's `account_id`.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy.orm import Session

from models.document import Document
from repositories.vector_store import Chunk, VectorStore
from services.document_processor import DeferProcessingError
from services.embeddings import EmbeddingRouter, EmbeddingUnavailableError
from services.extraction.pdf_text import extract_pdf_text

logger = logging.getLogger(__name__)

DOCUMENT_TYPE_LABELS = {
    "nfce": "Cupom fiscal (NFC-e)",
    "nfe": "Nota fiscal (NF-e)",
    "nfse": "Nota fiscal de serviço (NFS-e)",
    "grocery_receipt": "Cupom de supermercado",
    "pharmacy_receipt": "Recibo de farmácia",
    "energy_bill": "Conta de energia",
    "water_bill": "Conta de água",
    "telecom_bill": "Conta de telefone/internet",
    "bank_slip": "Boleto",
    "payment_receipt": "Comprovante de pagamento",
    "other": "Documento",
}

PAYMENT_STATUS_LABELS = {
    "paid": "pago",
    "pending": "a pagar",
    "overdue": "vencido",
    "cancelled": "cancelado",
    "unknown": "não informado",
}


def format_brl(value: Any) -> str | None:
    """'1234.5' → 'R$ 1.234,50'."""
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    text = f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {text}"


def _format_date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).strftime("%d/%m/%Y")
    except ValueError:
        return str(value)


def _summary_header(document: Document, result: dict[str, Any]) -> list[str]:
    doc_type = result.get("document_type") or document.document_type or "other"
    lines = [f"{DOCUMENT_TYPE_LABELS.get(doc_type, 'Documento')}: {result.get('description') or document.original_filename}"]

    if establishment := result.get("establishment"):
        tax_id = result.get("establishment_tax_id")
        lines.append(f"Emissor: {establishment}" + (f" (CNPJ/CPF {tax_id})" if tax_id else ""))

    dates = [
        f"{label}: {formatted}"
        for label, key in (("Emissão", "issued_at"), ("Vencimento", "due_date"), ("Pago em", "paid_at"))
        if (formatted := _format_date(result.get(key)))
    ]
    if dates:
        lines.append(" | ".join(dates))

    return lines


def build_summary_chunks(document: Document, chunk_size: int) -> list[str]:
    """Readable summary of the extraction, in parts of up to ~chunk_size characters."""
    result = (document.extracted_data or {}).get("result")
    if not result:
        return []

    header = _summary_header(document, result)
    body: list[str] = []

    if total := format_brl(result.get("total_amount")):
        status = PAYMENT_STATUS_LABELS.get(result.get("payment_status") or "unknown", "não informado")
        body.append(f"Valor total: {total} ({status})")

    extras = {k: v for k, v in (result.get("metadata") or {}).items() if isinstance(v, (str, int, float)) and v}
    skip = {"source", "model", "input_mode", "access_key", "authorization_status", "series"}
    details = [f"{key}: {value}" for key, value in extras.items() if key not in skip]
    if details:
        body.append("Detalhes: " + "; ".join(details))

    item_lines = []
    for item in result.get("items") or []:
        line = f"- {item.get('description')}"
        if item.get("quantity") is not None:
            line += f" | qtd {Decimal(str(item['quantity'])).normalize()} {item.get('unit') or ''}".rstrip()
        if price := format_brl(item.get("unit_price")):
            line += f" | unitário {price}"
        if total_price := format_brl(item.get("total_price")):
            line += f" | total {total_price}"
        item_lines.append(line)

    header_text = "\n".join(header)
    chunks: list[str] = []
    current = [header_text, *body]
    if item_lines:
        current.append("Itens:")

    for line in item_lines:
        if len("\n".join(current)) + len(line) > chunk_size and len(current) > 2:
            chunks.append("\n".join(current))
            current = [header_text, "Itens (continuação):"]
        current.append(line)
    chunks.append("\n".join(current))

    return chunks


def build_chunks(document: Document, content: bytes, *, chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    texts = [("summary", text) for text in build_summary_chunks(document, chunk_size)]

    if document.mime_type == "application/pdf" and (pdf_text := extract_pdf_text(content)):
        splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        prefix = f"Texto do documento {document.original_filename}:\n"
        texts.extend(("text", prefix + part) for part in splitter.split_text(pdf_text))

    return [
        Chunk(document_id=document.id, index=index, content=text, kind=kind, document_type=document.document_type)
        for index, (kind, text) in enumerate(texts)
    ]


class IndexingProcessor:
    """`DocumentProcessor` of the indexing jobs."""

    def __init__(
        self,
        embeddings: EmbeddingRouter,
        store: VectorStore,
        *,
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
    ) -> None:
        self.embeddings = embeddings
        self.store = store
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._collection_ready = False

    def process(self, session: Session, document: Document, content: bytes) -> None:
        chunks = build_chunks(document, content, chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)

        if chunks:
            try:
                vectors = self.embeddings.embed_documents([chunk.content for chunk in chunks])
            except EmbeddingUnavailableError as exc:
                # Unavailable models are not the document's fault: postpone without using an attempt.
                raise DeferProcessingError(str(exc), exc.cooldown_seconds) from exc

            if not self._collection_ready:
                self.store.ensure_collection({name: len(vecs[0]) for name, vecs in vectors.items()})
                self._collection_ready = True

            stored = self.store.replace_document(document.account_id, document.id, chunks, vectors)
        else:
            stored = 0

        document.indexed_at = datetime.now(UTC)
        logger.info("Document indexed | document=%s chunks=%s", document.id, stored)
