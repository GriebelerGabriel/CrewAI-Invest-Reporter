"""Full report of one stock: resolve → collect (no LLM) → analyze (LLM) → write and remember."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Protocol

from crewai_invest_reporter.domain.models import Analysis, Rating, ResearchDossier, Stock
from crewai_invest_reporter.services.report_writer import ReportWriter
from crewai_invest_reporter.services.research import ResearchCollector

logger = logging.getLogger(__name__)

Analyzer = Callable[[ResearchDossier, date], Analysis]


class RatingStore(Protocol):
    def save_rating(self, rating: Rating) -> None: ...


class AssetReportService:
    def __init__(
        self,
        analyze: Analyzer,
        resolve: Callable[[str], Stock],
        collector: ResearchCollector,
        writer: ReportWriter,
        ratings: RatingStore | None = None,
        model: str = "",
    ) -> None:
        self._analyze = analyze
        self._resolve = resolve
        self._collector = collector
        self._writer = writer
        self._ratings = ratings
        self._model = model

    def collect(self, ticker: str) -> ResearchDossier:
        """Everything the agents will read, gathered without any LLM call."""
        return self._collector.collect(self._resolve(ticker))

    def generate(self, ticker: str, today: date | None = None) -> Path:
        today = today or date.today()
        dossier = self.collect(ticker)
        analysis = self._analyze(dossier, today)
        path = self._writer.write(dossier.stock.report_name, analysis.report)
        if self._ratings is not None:
            self._remember(dossier, analysis, today)
        return path

    def _remember(self, dossier: ResearchDossier, analysis: Analysis, today: date) -> None:
        if analysis.verdict is None:
            logger.warning("%s: no structured verdict, rating not saved", dossier.stock.ticker)
            return
        metrics = dict(dossier.fundamentals.metrics) if dossier.fundamentals else {}
        self._ratings.save_rating(
            Rating(
                day=today,
                ticker=dossier.stock.ticker,
                verdict=analysis.verdict,
                price=metrics.get("currentPrice"),
                metrics=metrics,
                model=self._model,
                report=analysis.report,
            )
        )
