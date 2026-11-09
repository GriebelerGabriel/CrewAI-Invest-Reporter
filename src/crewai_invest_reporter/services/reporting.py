"""Full report of one stock: resolve → collect (no LLM) → analyze (LLM) → write and remember."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

from crewai_invest_reporter.domain.models import Analysis, Rating, ResearchDossier, Stock
from crewai_invest_reporter.providers.database import Database
from crewai_invest_reporter.services.research import ResearchCollector

logger = logging.getLogger(__name__)


class AssetReportService:
    def __init__(
        self,
        analyze: Callable[[ResearchDossier, date], Analysis],
        resolve: Callable[[str], Stock],
        collector: ResearchCollector,
        reports_dir: Path,
        ratings: Database,
        model: str,
    ) -> None:
        self._analyze = analyze
        self._resolve = resolve
        self._collector = collector
        self._reports_dir = reports_dir
        self._ratings = ratings
        self._model = model

    def collect(self, ticker: str) -> ResearchDossier:
        """Everything the agents will read, gathered without any LLM call."""
        return self._collector.collect(self._resolve(ticker))

    def generate(self, ticker: str, today: date) -> Path:
        """Write ``reports/<TICKER>_investment_report.md`` and save the verdict, if parsed."""
        dossier = self.collect(ticker)
        analysis = self._analyze(dossier, today)
        path = self._reports_dir / f"{dossier.stock.ticker}_investment_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(analysis.report.strip() + "\n", encoding="utf-8")
        if analysis.verdict is None:
            logger.warning("%s: no structured verdict, rating not saved", dossier.stock.ticker)
            return path
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
        return path
