"""Turn a ticker into the listed company behind it."""

from __future__ import annotations

import logging
from typing import Protocol

from crewai_invest_reporter.domain.errors import InvalidTickerError, ProviderError
from crewai_invest_reporter.domain.models import Stock
from crewai_invest_reporter.domain.tickers import normalize_ticker
from crewai_invest_reporter.providers.cvm import RegistryEntry

logger = logging.getLogger(__name__)


class CompanyRegistry(Protocol):
    def lookup(self, ticker: str) -> RegistryEntry | None: ...


class StockResolver:
    def __init__(self, registry: CompanyRegistry) -> None:
        self._registry = registry

    def resolve(self, ticker: str) -> Stock:
        """The listed company behind a ticker, with its name and CNPJ from CVM's registry."""
        ticker = normalize_ticker(ticker)
        try:
            company = self._registry.lookup(ticker)
        except ProviderError as exc:
            # CVM offline: still report on news and fundamentals, just without filings.
            logger.warning("CVM registry unavailable, %s has no CNPJ: %s", ticker, exc)
            return Stock(ticker)
        if company is None:
            raise InvalidTickerError(f"{ticker} is not a company listed in CVM's registry")
        return Stock(ticker, company.name, company.cnpj)
