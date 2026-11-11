"""CNPJ/CPF (Brazilian company/person tax IDs): only numbers with valid check digits are accepted.

The CNPJ groups entries from the same store (standardized name). A misread
or fake value ("00000000000000") would merge different companies.
"""


def _digit(numbers: str, weights: list[int]) -> int:
    remainder = sum(int(n) * w for n, w in zip(numbers, weights, strict=True)) % 11
    return 0 if remainder < 2 else 11 - remainder


def is_valid_cnpj(digits: str) -> bool:
    if len(digits) != 14 or not digits.isdigit() or len(set(digits)) == 1:
        return False
    first = _digit(digits[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = _digit(digits[:13], [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return digits[12:] == f"{first}{second}"


def is_valid_cpf(digits: str) -> bool:
    if len(digits) != 11 or not digits.isdigit() or len(set(digits)) == 1:
        return False
    first = (sum(int(d) * w for d, w in zip(digits[:9], range(10, 1, -1), strict=True)) * 10) % 11 % 10
    second = (sum(int(d) * w for d, w in zip(digits[:10], range(11, 1, -1), strict=True)) * 10) % 11 % 10
    return digits[9:] == f"{first}{second}"


def valid_tax_id(value: str | None) -> str | None:
    """Digits only, if it is a valid CNPJ or CPF; otherwise None."""
    digits = "".join(ch for ch in value or "" if ch.isdigit())
    return digits if is_valid_cnpj(digits) or is_valid_cpf(digits) else None
