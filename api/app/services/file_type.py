"""File type detection by content (magic bytes).

The `Content-Type` and the extension sent by the client are not trustworthy;
the accepted type is always the one detected here.
"""

from dataclasses import dataclass

_UTF8_BOM = b"\xef\xbb\xbf"

# Enough bytes to identify every supported type.
SNIFF_BYTES = 512


@dataclass(frozen=True)
class FileType:
    mime_type: str
    extension: str


PDF = FileType("application/pdf", "pdf")
PNG = FileType("image/png", "png")
JPEG = FileType("image/jpeg", "jpg")
XML = FileType("application/xml", "xml")

SUPPORTED_TYPES = (PDF, PNG, JPEG, XML)


def detect_file_type(head: bytes) -> FileType | None:
    if head.startswith(b"%PDF-"):
        return PDF

    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG

    if head.startswith(b"\xff\xd8\xff"):
        return JPEG

    # XML (e.g. NF-e): <?xml declaration or root element, with optional BOM/whitespace.
    text = head.removeprefix(_UTF8_BOM).lstrip()
    if text.startswith(b"<?xml") or (text[:1] == b"<" and text[1:2].isalpha()):
        return XML

    return None
