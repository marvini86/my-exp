import pytest

from services.document import DocumentService
from services.file_type import JPEG, PDF, PNG, XML, detect_file_type


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"%PDF-1.7\n...", PDF),
        (b"\x89PNG\r\n\x1a\n\x00\x00", PNG),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", JPEG),
        (b'<?xml version="1.0"?><nfeProc/>', XML),
        (b"\xef\xbb\xbf  \n<?xml version='1.0'?>", XML),
        (b"<nfeProc versao='4.00'>", XML),
    ],
)
def test_detects_supported_types(head, expected):
    assert detect_file_type(head) is expected


@pytest.mark.parametrize(
    "head",
    [
        b"MZ\x90\x00",  # Windows executable
        b"PK\x03\x04",  # zip/docx
        b"GIF89a",
        b"plain text",
        b"< not xml",
        b"",
    ],
)
def test_rejects_unsupported_types(head):
    assert detect_file_type(head) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("nota.pdf", "nota.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\conta luz.pdf", "conta luz.pdf"),
        ("evil\x00name\n.pdf", "evilname.pdf"),
        ("", "document"),
        (None, "document"),
        ("a" * 300 + ".pdf", "a" * 255),
    ],
)
def test_sanitize_filename(raw, expected):
    assert DocumentService._sanitize_filename(raw) == expected
