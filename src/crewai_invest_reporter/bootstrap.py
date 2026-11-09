"""Composition root: the single place where concrete implementations are wired together.

Components are created lazily and shared, so expensive state (CVM registry and filing index,
HTTP sessions) is built once per run even when handling many stocks.
"""

from __future__ import annotations

from functools import cached_property

from crewai import LLM, BaseLLM

from crewai_invest_reporter.crews.asset_report.crew import analyze
from crewai_invest_reporter.crews.daily_news.crew import summarize
from crewai_invest_reporter.providers.cvm import (
    CvmCompanyFilingsProvider,
    CvmCompanyRegistry,
    CvmOpenDataClient,
)
from crewai_invest_reporter.providers.database import Database
from crewai_invest_reporter.providers.documents import HttpDocumentReader
from crewai_invest_reporter.providers.google_news import GoogleNewsProvider
from crewai_invest_reporter.providers.investidor10 import Investidor10Provider
from crewai_invest_reporter.providers.smtp import SmtpMailer
from crewai_invest_reporter.services.daily import DailyDigestService
from crewai_invest_reporter.services.reporting import AssetReportService
from crewai_invest_reporter.services.research import ResearchCollector
from crewai_invest_reporter.services.stocks import StockResolver
from crewai_invest_reporter.services.subscriptions import SubscriptionService
from crewai_invest_reporter.settings import Settings
from crewai_invest_reporter.watchlist import read_watchlist


class Container:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # --- providers -----------------------------------------------------------------------

    @cached_property
    def _cvm(self) -> CvmOpenDataClient:
        return CvmOpenDataClient.with_cache(self.settings.cache_dir)

    @cached_property
    def resolver(self) -> StockResolver:
        return StockResolver(CvmCompanyRegistry(self._cvm))

    @cached_property
    def database(self) -> Database:
        return Database(self.settings.database_file)

    def tickers(self) -> list[str]:
        return read_watchlist(self.settings.watchlist_file)

    def choices(self) -> list[tuple[str, str]]:
        """(ticker, company name) offered on the sign-up page; names come from past digests."""
        names = self.database.company_names()
        return [(ticker, names.get(ticker, "")) for ticker in self.tickers()]

    @cached_property
    def collector(self) -> ResearchCollector:
        timeout = self.settings.http_timeout_seconds
        return ResearchCollector(
            news=GoogleNewsProvider(timeout=timeout),
            fundamentals=Investidor10Provider(timeout=timeout),
            filings=CvmCompanyFilingsProvider(self._cvm),
            documents=HttpDocumentReader(),
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

    @property
    def model_name(self) -> str:
        config = self.settings.llm
        return config.bifrost_model if config.uses_bifrost else (config.model or "crewai-default")

    # --- services ------------------------------------------------------------------------

    @cached_property
    def report_service(self) -> AssetReportService:
        return AssetReportService(
            analyze=lambda dossier, today: analyze(dossier, today, llm=self.llm),
            resolve=self.resolver.resolve,
            collector=self.collector,
            reports_dir=self.settings.reports_dir,
            ratings=self.database,
            model=self.model_name,
        )

    @cached_property
    def daily_digests(self) -> DailyDigestService:
        return DailyDigestService(
            store=self.database,
            resolve=self.resolver.resolve,
            collector=self.collector,
            summarize=lambda dossier, day, days: summarize(dossier, day, days, llm=self.llm),
        )

    @cached_property
    def subscriptions(self) -> SubscriptionService:
        return SubscriptionService(
            db=self.database,
            mailer=SmtpMailer(self.settings.mail.require()),
            tickers=self.tickers,
            public_url=self.settings.public_url,
        )
