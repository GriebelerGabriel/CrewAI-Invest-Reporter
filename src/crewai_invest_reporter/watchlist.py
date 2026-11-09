"""The fixed list of stocks to report on: a text file with one B3 ticker per line.

Blank lines and anything after ``#`` are ignored, so the file can carry notes::

    PETR4
    VALE3   # mineração
"""

from __future__ import annotations

from pathlib import Path

from crewai_invest_reporter.domain.errors import ConfigurationError, InvalidTickerError
from crewai_invest_reporter.domain.tickers import normalize_ticker


def read_watchlist(path: Path) -> list[str]:
    """Tickers in file order, normalized and without duplicates."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise ConfigurationError(
            f"Watchlist {path} not found: create it with one ticker per line (e.g. PETR4)."
        ) from None
    tickers: list[str] = []
    for number, line in enumerate(lines, start=1):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        try:
            ticker = normalize_ticker(entry)
        except InvalidTickerError:
            raise ConfigurationError(f"{path}:{number}: {entry!r} is not a B3 ticker") from None
        if ticker not in tickers:
            tickers.append(ticker)
    if not tickers:
        raise ConfigurationError(f"Watchlist {path} has no tickers.")
    return tickers
