"""PDF and image extraction via LLM (LangChain), with fallback between models.

- PDF with a text layer → only the text is sent (cheaper, any model);
- scanned PDF / image → the file is sent to a vision model.

Model choice, fallback and cooldowns live in `LLMRouter`. If every
model is rate limited, the job is postponed (`DeferProcessingError`) without
using an attempt.

The LLM answers with `LLMExtraction` (a simple schema). Converting it to the
official `ExtractedDocument` contract validates limits and types (Decimal).
Calculations and business rules stay outside the LLM.
"""

import base64
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError

from core.config import AgentSettings, OpenRouterSettings
from models.financial import PaymentStatus
from schemas.extraction import DocumentType, ExtractedDocument, ExtractedItem, SpendingCategory
from services.document_processor import DeferProcessingError, PermanentProcessingError
from services.extraction.access_key import find_access_key
from services.extraction.model_catalog import ModelCatalog
from services.extraction.pdf_text import extract_pdf_text
from services.extraction.prompts import (
    DOCUMENT_TEXT_TEMPLATE,
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_PROMPT,
)
from services.extraction.tax_id import valid_tax_id
from services.extraction.validation import consistency_issues
from services.llm_router import LLMOutputError, LLMRequestError, LLMRouter, LLMUnavailableError

if TYPE_CHECKING:
    from core.config import Settings

logger = logging.getLogger(__name__)

# Extra models when the reading is inconsistent (LLM_EXTRACTION_QUALITY_RETRIES).
DEFAULT_QUALITY_RETRIES = 2
# After this, keep the best reading instead of trying another model (do not hold the queue).
QUALITY_RETRY_TIME_BUDGET_SECONDS = 90

SUPPORTED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}

_CENTS = Decimal("0.01")
_QUANTITY_PLACES = Decimal("0.001")
_UNIT_PRICE_PLACES = Decimal("0.0001")


# ---------------------------------------------------------------------------
# LLM output schema
# ---------------------------------------------------------------------------


class LLMItem(BaseModel):
    description: str
    quantity: float | None = None
    unit: str | None = None
    unit_price: float | None = None
    total_price: float | None = None
    brand: str | None = None
    sku: str | None = Field(default=None, description="Código de barras (EAN) do produto, se impresso")


class LLMField(BaseModel):
    name: str
    value: str


class LLMExtraction(BaseModel):
    """Data extracted from a financial document."""

    readable: bool = Field(description="false se não for documento financeiro ou estiver ilegível")
    unreadable_reason: str | None = None
    document_type: DocumentType
    description: str | None = Field(default=None, description="Descrição curta, ex.: 'Conta de energia jan/2024'")
    establishment: str | None = Field(default=None, description="Razão social da empresa emissora")
    trade_name: str | None = Field(default=None, description="Nome fantasia (nome conhecido da loja), se impresso")
    establishment_tax_id: str | None = Field(default=None, description="CNPJ/CPF do emissor, só dígitos")
    access_key: str | None = Field(default=None, description="Chave de acesso (44 dígitos) de NF-e/NFC-e ou de conta de energia/telefone, se impressa")
    issued_at: date | None = None
    due_date: date | None = None
    paid_at: datetime | None = None
    payment_status: PaymentStatus = PaymentStatus.UNKNOWN
    total_amount: float | None = None
    discount_amount: float | None = Field(
        default=None, description="Desconto total impresso (ex.: 'DESCONTO R$ 3,78'), se houver"
    )
    currency: str = "BRL"
    # Required (no default): with an optional field, free models omitted it
    # even when the items were readable in the photo.
    items: list[LLMItem] = Field(
        description="TODOS os produtos/linhas de item, na ordem impressa (lista vazia só se o documento não tiver itens)"
    )
    items_count_printed: int | None = Field(
        default=None, description="Quantidade total de itens impressa no cupom (ex.: 'Qtd. total de itens 10')"
    )
    # Text (not enum): a value outside the list must not break the whole extraction.
    category: str | None = Field(
        default=None, description="Categoria do gasto: " + ", ".join(c.value for c in SpendingCategory)
    )
    extra_fields: list[LLMField] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Extrator
# ---------------------------------------------------------------------------


class LLMDocumentExtractor:
    def __init__(
        self,
        catalog: ModelCatalog,
        llm_factory: Callable[[str], BaseChatModel],
        *,
        defer_seconds: float = 300,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        quality_retries: int = DEFAULT_QUALITY_RETRIES,
    ) -> None:
        self.router = LLMRouter(catalog, llm_factory, empty_catalog_delay=defer_seconds, now=now)
        self.quality_retries = quality_retries

    @classmethod
    def from_router(cls, router: LLMRouter, *, quality_retries: int = DEFAULT_QUALITY_RETRIES) -> "LLMDocumentExtractor":
        extractor = cls.__new__(cls)
        extractor.router = router
        extractor.quality_retries = quality_retries
        return extractor

    @classmethod
    def from_settings(cls, openrouter: OpenRouterSettings, agent: AgentSettings) -> "LLMDocumentExtractor":
        """OpenRouter only (AGENT_ALLOWED_MODELS). Prefer `for_settings`."""
        return cls.from_router(LLMRouter.from_settings(openrouter, agent))

    @classmethod
    def for_settings(cls, settings: "Settings") -> "LLMDocumentExtractor":
        """Vision models from .env (LLM_VISION_MODELS, several providers) or, without them, OpenRouter."""
        return cls.from_router(
            LLMRouter.for_purpose(settings, vision=True), quality_retries=settings.llm.extraction_quality_retries
        )

    def supports(self, mime_type: str) -> bool:
        return mime_type in SUPPORTED_MIME_TYPES

    def extract(self, content: bytes, mime_type: str) -> ExtractedDocument:
        """Extracts with the first available model; an inconsistent reading (items sum ≠
        total, items read ≠ printed) is redone with the next model, up to `quality_retries`
        times. If none gets it right, the reading with the fewest problems is kept (with the warnings)."""
        messages, input_mode = self._build_messages(content, mime_type)
        tried: list[str] = []
        best: tuple[ExtractedDocument, list[str]] | None = None

        started = time.monotonic()
        for _ in range(1 + self.quality_retries):
            if best is not None and time.monotonic() - started > QUALITY_RETRY_TIME_BUDGET_SECONDS:
                logger.info("Quality retries skipped: time budget used | tried=%s", tried)
                break
            try:
                result, model_id = self.router.structured(
                    messages, LLMExtraction, needs_vision=input_mode != "pdf_text", exclude=tried
                )
            except LLMUnavailableError as exc:
                if best:
                    break  # there is already a (imperfect) reading: better than postponing the document
                raise DeferProcessingError(str(exc), exc.delay_seconds) from exc
            except LLMOutputError as exc:
                if best:
                    break
                raise PermanentProcessingError(f"No AI model produced a valid extraction: {exc}") from exc
            except LLMRequestError as exc:
                raise PermanentProcessingError(f"Document rejected by the AI model: {exc}") from exc

            tried.append(model_id)
            document = self._finish(result, model_id, input_mode)
            issues = consistency_issues(document)
            if not issues:
                return document
            logger.info("Inconsistent extraction; trying another model | model=%s issues=%s", model_id, issues)
            if best is None or len(issues) < len(best[1]):
                best = (document, issues)

        assert best is not None
        best[0].metadata["models_tried"] = ", ".join(tried)
        return best[0]

    def _build_messages(self, content: bytes, mime_type: str) -> tuple[list, str]:
        system = SystemMessage(content=EXTRACTION_SYSTEM_PROMPT)
        instruction: dict[str, Any] = {"type": "text", "text": EXTRACTION_USER_PROMPT}

        if mime_type == "application/pdf" and (text := extract_pdf_text(content)):
            body = {"type": "text", "text": DOCUMENT_TEXT_TEMPLATE.format(text=text)}
            return [system, HumanMessage(content=[instruction, body])], "pdf_text"

        encoded = base64.b64encode(content).decode("ascii")
        if mime_type == "application/pdf":
            block = {"type": "file", "base64": encoded, "mime_type": mime_type, "filename": "document.pdf"}
            mode = "pdf_file"
        else:
            block = {"type": "image", "base64": encoded, "mime_type": mime_type}
            mode = "image"

        return [system, HumanMessage(content=[instruction, block])], mode

    def _finish(self, result: LLMExtraction, model_id: str, input_mode: str) -> ExtractedDocument:
        if not result.readable:
            raise PermanentProcessingError(
                f"Document not readable as financial document: {result.unreadable_reason or 'no reason given'}"
            )

        try:
            extracted = self._to_contract(result)
        except (ValidationError, ArithmeticError, ValueError) as exc:
            raise PermanentProcessingError(f"LLM returned invalid data: {exc}") from exc

        extracted.metadata.update({"source": "llm", "model": model_id, "input_mode": input_mode})
        logger.info("LLM extraction succeeded | model=%s mode=%s", model_id, input_mode)
        return extracted

    @staticmethod
    def _to_contract(result: LLMExtraction) -> ExtractedDocument:
        extras = {f.name: value for f in result.extra_fields if f.name and (value := _plain(f.value))}
        # Smaller models sometimes put the issuer/CNPJ in extra_fields: promote to the proper field.
        legal_name = _plain(result.establishment) or _pop_alias(extras, _ESTABLISHMENT_ALIASES)
        trade_name = _plain(result.trade_name) or _pop_alias(extras, _TRADE_NAME_ALIASES)
        # Only CNPJ/CPF with valid check digits (a misread one would merge different stores).
        tax_id = valid_tax_id(result.establishment_tax_id or _pop_alias(extras, _TAX_ID_ALIASES))
        document_type = result.document_type

        # A valid access key (check digit verified) is the source of truth for type and CNPJ.
        key = find_access_key(result.access_key, *extras.values())
        if key:
            document_type = key.document_type
            tax_id = key.cnpj
            for alias in list(extras):
                if key.key in "".join(ch for ch in extras[alias] if ch.isdigit()):
                    extras.pop(alias)
            extras.update({"access_key": key.key, "series": key.series, "number": key.number})

        if legal_name:
            extras["legal_name"] = legal_name
        if result.items_count_printed is not None:
            extras["items_count_printed"] = str(result.items_count_printed)
        if result.discount_amount:
            extras["discount"] = str(_decimal(abs(result.discount_amount), _CENTS))

        return ExtractedDocument(
            document_type=document_type,
            description=_clip(_plain(result.description), 500),
            # The trade name is how the family knows the store; the legal name goes into the metadata.
            establishment=_clip(trade_name or legal_name, 255),
            establishment_tax_id=tax_id,
            issued_at=result.issued_at,
            due_date=result.due_date,
            paid_at=result.paid_at,
            payment_status=result.payment_status,
            total_amount=_decimal(result.total_amount, _CENTS),
            currency=(result.currency or "BRL").upper()[:3],
            items=[
                ExtractedItem(
                    description=_clip(_plain(item.description), 500) or "Item",
                    brand=_clip(_plain(item.brand), 255),
                    sku=_clip(item.sku, 100),
                    quantity=_decimal(item.quantity, _QUANTITY_PLACES),
                    unit=_clip(item.unit, 30),
                    unit_price=_decimal(item.unit_price, _UNIT_PRICE_PLACES),
                    total_price=_decimal(item.total_price, _CENTS),
                )
                for item in result.items
                if item.description and item.description.strip()
            ],
            category=_category(result.category),
            metadata=extras,
        )


def _category(value: str | None) -> SpendingCategory | None:
    if not value:
        return None
    try:
        return SpendingCategory(value.strip().lower())
    except ValueError:
        return None


_ESTABLISHMENT_ALIASES = {"issuer", "emissor", "empresa", "establishment", "fornecedor", "razao_social", "company"}
_TRADE_NAME_ALIASES = {"nome_fantasia", "trade_name", "fantasia"}
_TAX_ID_ALIASES = {"cnpj", "cpf", "tax_id", "cnpj_emissor", "issuer_cnpj", "establishment_tax_id"}


def _normalize_key(key: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in key.strip().lower()).strip("_")


def _pop_alias(extras: dict[str, str], aliases: set[str]) -> str | None:
    for key in list(extras):
        if _normalize_key(key) in aliases and extras[key]:
            return extras.pop(key)
    return None


_HTML_TAG = re.compile(r"<[^>]+>")


def _plain(value: str | None) -> str | None:
    """Removes HTML tags that some PDFs/models leave in the text (e.g. "<br/>")."""
    if value is None:
        return None
    return re.sub(r"\s+", " ", _HTML_TAG.sub(" ", value)).strip() or None


def _decimal(value: float | None, places: Decimal) -> Decimal | None:
    # str() avoids the float binary error (63.43 → "63.43").
    return None if value is None else Decimal(str(value)).quantize(places, rounding=ROUND_HALF_UP)


def _clip(value: str | None, length: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value[:length] or None


def _digits(value: str | None) -> str | None:
    digits = "".join(ch for ch in value or "" if ch.isdigit())
    return digits[:20] or None
