"""Access key of a Brazilian electronic fiscal document (44 digits) — a deterministic source, no AI.

Models: 55 NF-e, 65 NFC-e, 66 NF3e (energy bill), 62 NFCom (phone/internet).

Layout: cUF(2) YYMM(4) CNPJ(14) model(2) series(3) number(9) tpEmis(1) code(8) DV(1).
The check digit (mod 11) ensures the reading did not swap a digit; only a
valid key overrides the document type and CNPJ suggested by the LLM.
"""

import re
from dataclasses import dataclass

from schemas.extraction import DocumentType

_MODELS = {
    "55": DocumentType.NFE,
    "65": DocumentType.NFCE,
    "66": DocumentType.ENERGY_BILL,
    "62": DocumentType.TELECOM_BILL,
}
_CANDIDATE = re.compile(r"(?:\d[\s.]*){44}")


@dataclass(frozen=True)
class AccessKey:
    key: str
    cnpj: str
    document_type: DocumentType
    series: str
    number: str


def _check_digit(first_43: str) -> int:
    total, weight = 0, 2
    for digit in reversed(first_43):
        total += int(digit) * weight
        weight = 2 if weight == 9 else weight + 1
    remainder = total % 11
    return 0 if remainder < 2 else 11 - remainder


def parse_access_key(value: str | None) -> AccessKey | None:
    """Valid key (check digit verified and known model) or None."""
    digits = "".join(ch for ch in value or "" if ch.isdigit())
    if len(digits) != 44 or _check_digit(digits[:43]) != int(digits[43]):
        return None
    model = digits[20:22]
    if model not in _MODELS:
        return None
    return AccessKey(
        key=digits,
        cnpj=digits[6:20],
        document_type=_MODELS[model],
        series=digits[22:25].lstrip("0") or "0",
        number=digits[25:34].lstrip("0") or "0",
    )


def find_access_key(*texts: str | None) -> AccessKey | None:
    """First valid key found in the texts (LLM fields, extras...)."""
    for text in texts:
        for match in _CANDIDATE.finditer(text or ""):
            if key := parse_access_key(match.group()):
                return key
    return None
