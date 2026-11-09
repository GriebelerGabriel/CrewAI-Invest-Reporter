"""B3 ticker normalization."""

from __future__ import annotations

import re

from crewai_invest_reporter.domain.errors import InvalidTickerError

# Four letters + share-class digits (3 ON, 4 PN, 11 units...), with an optional trailing "F"
# for the fractional market.
_B3_TICKER = re.compile(r"^([A-Z0-9]{4}\d{1,2})F?$")


def normalize_ticker(raw: str) -> str:
    """Return the canonical B3 ticker (``petr4.sa`` -> ``PETR4``) or raise InvalidTickerError.

    Validation also guarantees the ticker is safe to use in file names and URLs.
    """
    candidate = raw.strip().upper().removesuffix(".SA")
    match = _B3_TICKER.match(candidate)
    if not match:
        raise InvalidTickerError(f"Invalid B3 ticker: {raw!r}")
    return match.group(1)
