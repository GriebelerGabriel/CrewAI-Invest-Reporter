"""Helpers to parse numbers as they are written on Brazilian financial websites."""

from __future__ import annotations

import re

_THOUSANDS_ONLY = re.compile(r"^-?\d{1,3}(\.\d{3})+$")
_NOISE = ("R$", "%", " ", "\xa0", "+")


def parse_br_number(text: str | None) -> float | None:
    """Parse ``"1.234,56"``, ``"R$ 10,50"``, ``"12,3%"``, ``"5.4"`` or ``"1.000"`` into a float.

    Brazilian notation uses ``.`` for thousands and ``,`` for decimals. When only a dot is
    present it is treated as a thousands separator only if it groups exactly three digits.
    """
    if not text:
        return None
    cleaned = text
    for token in _NOISE:
        cleaned = cleaned.replace(token, "")
    if not cleaned:
        return None

    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif _THOUSANDS_ONLY.match(cleaned):
        cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_br_percent(text: str | None) -> float | None:
    """Parse a percentage into a ratio (``"12,5%"`` -> ``0.125``)."""
    value = parse_br_number(text)
    return None if value is None else round(value / 100.0, 6)
