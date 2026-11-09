"""Composition root: the single place where concrete implementations are wired together.

Components are created lazily and shared, so expensive state (CVM registries, filing
indexes, HTTP sessions, fetched pages) is built once per run even for many assets.
"""

from __future__ import annotations

from functools import cached_property

from crewai import LLM, BaseLLM

from crewai_invest_reporter.crews.asset_report.crew import AssetReportCrew, asset_report_inputs
from crewai_invest_reporter.domain.ports import PortfolioProvider
from crewai_invest_reporter.providers.assets import OfficialFilingsRouter, RegistryAssetResolver
from crewai_invest_reporter.providers.cache import DiskCachedFetcher
from crewai_invest_reporter.providers.cvm import (
    CvmCompanyFilingsProvider,
    CvmCompanyRegistry,
    CvmFiiRegistry,
    CvmOpenDataClient,
)
from crewai_invest_reporter.providers.documents import CachedDocumentReader, HttpDocumentReader
from crewai_invest_reporter.providers.fundosnet import FundosNetFilingsProvider
from crewai_invest_reporter.providers.google_news import GoogleNewsProvider
from crewai_invest_reporter.providers.http import build_session
from crewai_invest_reporter.providers.investidor10 import Investidor10Provider
from crewai_invest_reporter.providers.pluggy import PluggyPortfolioProvider
from crewai_invest_reporter.providers.sec_edgar import SecEdgarFilingsProvider
from crewai_invest_reporter.providers.wallet import ManualPortfolioProvider, WalletFile
from crewai_invest_reporter.services.report_writer import ReportWriter
from crewai_invest_reporter.services.reporting import AssetReportService
from crewai_invest_reporter.services.research import ResearchCollector
from crewai_invest_reporter.settings import Settings


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # --- providers -----------------------------------------------------------------------

    @cached_property
    def fundamentals(self) -> Investidor10Provider:
        return Investidor10Provider(timeout=self.settings.http_timeout_seconds)

    @cached_property
    def _cvm(self) -> CvmOpenDataClient:
        return CvmOpenDataClient.with_cache(self.settings.cache_dir)

    @cached_property
    def _companies(self) -> CvmCompanyRegistry:
        return CvmCompanyRegistry(self._cvm)

    @cached_property
    def _fiis(self) -> CvmFiiRegistry:
        return CvmFiiRegistry(self._cvm)

    @cached_property
    def resolver(self) -> RegistryAssetResolver:
        return RegistryAssetResolver(
            self._companies, self._fiis, bdr_underlying=self.fundamentals.underlying_ticker
        )

    def asset_suggestions(self) -> list[tuple[str, str]]:
        """(ticker, name) of every listed company and FII, for the wallet page."""
        entries = {**self._fiis.tickers(), **self._companies.tickers()}
        return sorted((ticker, entry.name) for ticker, entry in entries.items())

    @cached_property
    def filings(self) -> OfficialFilingsRouter:
        sec_session = build_session({"User-Agent": self.settings.sec_user_agent})
        return OfficialFilingsRouter(
            company_filings=CvmCompanyFilingsProvider(self._cvm),
            fund_filings=FundosNetFilingsProvider(),
            foreign_filings=SecEdgarFilingsProvider(
                DiskCachedFetcher(self.settings.cache_dir / "sec", session=sec_session),
                session=sec_session,
            ),
        )

    @cached_property
    def collector(self) -> ResearchCollector:
        return ResearchCollector(
            news=GoogleNewsProvider(timeout=self.settings.http_timeout_seconds),
            fundamentals=self.fundamentals,
            filings=self.filings,
            documents=CachedDocumentReader(
                HttpDocumentReader(
                    domain_headers={"sec.gov": {"User-Agent": self.settings.sec_user_agent}}
                )
            ),
        )

    @cached_property
    def wallet(self) -> WalletFile:
        return WalletFile(self.settings.wallet_file)

    def portfolio_provider(self) -> PortfolioProvider:
        if self.settings.wallet_source == "pluggy":
            return PluggyPortfolioProvider.from_settings(self.settings.pluggy)
        return ManualPortfolioProvider(
            self.wallet,
            quote=lambda ticker: self.fundamentals.fetch(ticker).metrics["currentPrice"],
        )

    # --- LLM -----------------------------------------------------------------------------

    @cached_property
    def llm(self) -> BaseLLM | None:
        """An LLM routed through the Bifrost gateway, or None to keep CrewAI's defaults."""
        config = self.settings.llm
        if not config.uses_bifrost:
            return None
        config.validate()
        return LLM(
            model=config.bifrost_model,
            # Bifrost speaks the OpenAI API for every provider. Naming the provider keeps the
            # "provider/model" name intact: otherwise CrewAI strips "openai/" and sends
            # "anthropic/..." or "gemini/..." to those vendors' own SDKs.
            provider="openai",
            base_url=config.bifrost_base_url,
            api_key=config.bifrost_api_key,
            # The virtual key goes out as "Authorization: Bearer" (from api_key) and as x-bf-vk,
            # Bifrost's own header, which it reads whatever its auth setup.
            default_headers={"x-bf-vk": config.bifrost_api_key},
        )

    # --- crews and services --------------------------------------------------------------

    @cached_property
    def writer(self) -> ReportWriter:
        return ReportWriter(self.settings.reports_dir)

    @cached_property
    def asset_service(self) -> AssetReportService:
        return AssetReportService(
            crew_factory=lambda: AssetReportCrew(llm=self.llm).crew(),
            inputs_builder=asset_report_inputs,
            resolver=self.resolver,
            collector=self.collector,
            writer=self.writer,
        )
