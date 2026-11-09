"""CNPJ (Brazilian company tax id) helpers."""

from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D")


def cnpj_digits(raw: str | None) -> str | None:
    """Return the 14 CNPJ digits (``"33.000.167/0001-01"`` -> ``"33000167000101"``) or None."""
    if not raw:
        return None
    digits = _NON_DIGITS.sub("", raw)
    return digits if len(digits) == 14 and _has_valid_check_digits(digits) else None


def _has_valid_check_digits(digits: str) -> bool:
    if digits == digits[0] * 14:
        return False

    def check_digit(base: str, weights: list[int]) -> str:
        remainder = sum(int(d) * w for d, w in zip(base, weights, strict=True)) % 11
        return "0" if remainder < 2 else str(11 - remainder)

    first = check_digit(digits[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = check_digit(digits[:12] + first, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return digits[12:] == first + second
