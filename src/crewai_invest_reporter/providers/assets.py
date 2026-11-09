"""Asset identification and routing of official-filing lookups by asset class."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from crewai_invest_reporter.domain.cnpj import cnpj_digits
from crewai_invest_reporter.domain.errors import InvalidTickerError, ProviderError
from crewai_invest_reporter.domain.models import AssetClass, AssetRef, Filing
from crewai_invest_reporter.domain.ports import FilingsProvider
from crewai_invest_reporter.domain.tickers import is_bdr, is_unit_like, normalize_ticker
from crewai_invest_reporter.providers.cvm import RegistryEntry

logger = logging.getLogger(__name__)


class Registry(Protocol):
    def lookup(self, ticker: str) -> RegistryEntry | None: ...


class FundRegistry(Registry, Protocol):
    def lookup_cnpj(self, cnpj: str) -> RegistryEntry | None: ...


class RegistryAssetResolver:
    """Resolve tickers/CNPJs using official registries, falling back to ticker conventions.

    Registry failures (e.g. CVM offline) degrade gracefully to convention-based guesses,
    because a missing CNPJ only disables official filings, not the whole report.
    """

    def __init__(
        self,
        companies: Registry,
        fiis: FundRegistry,
        bdr_underlying: Callable[[str], str | None] = lambda _ticker: None,
    ) -> None:
        self._companies = companies
        self._fiis = fiis
        self._bdr_underlying = bdr_underlying

    def resolve(self, identifier: str) -> AssetRef:
        cnpj = cnpj_digits(identifier)
        if cnpj:
            return self._resolve_cnpj(cnpj)
        try:
            ticker = normalize_ticker(identifier)
        except InvalidTickerError:
            raise InvalidTickerError(f"Not a B3 ticker nor a CNPJ: {identifier!r}") from None
        return self._resolve_ticker(ticker)

    def _resolve_cnpj(self, cnpj: str) -> AssetRef:
        fii = self._safe(lambda: self._fiis.lookup_cnpj(cnpj))
        if fii:
            return AssetRef(cnpj, AssetClass.REAL_ESTATE_FUND, fii.name, cnpj)
        return AssetRef(cnpj, AssetClass.MUTUAL_FUND, None, cnpj)

    def _resolve_ticker(self, ticker: str) -> AssetRef:
        if is_bdr(ticker):
            return AssetRef(ticker, AssetClass.BDR, foreign_ticker=self._bdr_underlying(ticker))
        company = self._safe(lambda: self._companies.lookup(ticker))
        if company:
            return AssetRef(ticker, AssetClass.STOCK, company.name, company.cnpj)
        if is_unit_like(ticker):
            fii = self._safe(lambda: self._fiis.lookup(ticker))
            if fii:
                return AssetRef(ticker, AssetClass.REAL_ESTATE_FUND, fii.name, fii.cnpj)
            # A "11" ticker that is neither a company unit nor an FII is most likely an ETF.
            return AssetRef(ticker, AssetClass.ETF)
        return AssetRef(ticker, AssetClass.STOCK)

    @staticmethod
    def _safe(lookup):
        try:
            return lookup()
        except ProviderError as exc:
            logger.warning("Registry lookup failed, using ticker conventions: %s", exc)
            return None


class OfficialFilingsRouter:
    """Send each asset to the official source that covers it.

    Companies file with CVM (IPE); FIIs and other structured funds publish on FundosNET; the
    US companies behind BDRs file with the SEC. ETFs have no issuer filings and get none.
    """

    def __init__(
        self,
        company_filings: FilingsProvider,
        fund_filings: FilingsProvider,
        foreign_filings: FilingsProvider | None = None,
    ) -> None:
        self._routes: dict[AssetClass, FilingsProvider] = {
            AssetClass.STOCK: company_filings,
            AssetClass.REAL_ESTATE_FUND: fund_filings,
            AssetClass.MUTUAL_FUND: fund_filings,
        }
        if foreign_filings is not None:
            self._routes[AssetClass.BDR] = foreign_filings

    def list_filings(self, asset: AssetRef, *, days: int = 180, limit: int = 20) -> list[Filing]:
        provider = self._routes.get(asset.asset_class)
        if provider is None or not (asset.cnpj or asset.foreign_ticker):
            return []
        return provider.list_filings(asset, days=days, limit=limit)
