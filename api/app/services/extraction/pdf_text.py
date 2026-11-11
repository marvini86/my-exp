"""Text layer extraction from PDFs (pypdf, lightweight, no OCR).

Digitally generated PDFs (energy/water bills, bank slips) have embedded
text: sending it to the LLM is cheaper and faster than sending the file.
Scanned PDFs have no text and go to the vision model as a file.
"""

import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)

MAX_PAGES = 5
MAX_CHARS = 20_000
# Below this, we assume the PDF has no useful text (probably scanned).
MIN_USEFUL_CHARS = 200


def extract_pdf_text(content: bytes) -> str | None:
    """Text of the first pages, or None if there is no usable text."""
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            return None

        parts: list[str] = []
        total = 0
        for page in reader.pages[:MAX_PAGES]:
            page_text = (page.extract_text() or "").strip()
            parts.append(page_text)
            total += len(page_text)
            if total >= MAX_CHARS:
                break
    except Exception:
        # PDF malformed for pypdf: the model may still be able to read the file.
        logger.warning("PDF text extraction failed; falling back to file input", exc_info=True)
        return None

    text = "\n\n".join(parts).strip()[:MAX_CHARS]
    useful = sum(ch.isalnum() for ch in text)

    return text if useful >= MIN_USEFUL_CHARS else None
