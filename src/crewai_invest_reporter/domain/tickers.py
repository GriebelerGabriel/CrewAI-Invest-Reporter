"""B3 ticker normalization and classification rules."""

from __future__ import annotations

import re

from crewai_invest_reporter.domain.errors import InvalidTickerError

# Four letters + share-class digits (3 ON, 4 PN, 11 units/FIIs/ETFs, 34 BDRs...), with an
# optional trailing "F" for the fractional market.
_B3_TICKER = re.compile(r"^([A-Z0-9]{4}\d{1,2})F?$")
_BDR_SUFFIXES = ("31", "32", "33", "34", "35", "39")


def normalize_ticker(raw: str) -> str:
    """Return the canonical B3 ticker (``petr4.sa`` -> ``PETR4``) or raise InvalidTickerError.

    Validation also guarantees the ticker is safe to use in file names and URLs.
    """
    candidate = raw.strip().upper().removesuffix(".SA")
    match = _B3_TICKER.match(candidate)
    if not match:
        raise InvalidTickerError(f"Invalid B3 ticker: {raw!r}")
    return match.group(1)


def try_normalize_ticker(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        return normalize_ticker(raw)
    except InvalidTickerError:
        return None


def is_bdr(ticker: str) -> bool:
    return ticker.endswith(_BDR_SUFFIXES)


def is_unit_like(ticker: str) -> bool:
    """Tickers ending in 11 may be FIIs, ETFs or share units (e.g. TAEE11, KLBN11)."""
    return ticker.endswith("11")
