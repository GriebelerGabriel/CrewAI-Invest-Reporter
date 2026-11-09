"""Application services: orchestrate data providers, crews and report persistence."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from crewai_invest_reporter.domain.errors import InvestReporterError
from crewai_invest_reporter.domain.models import AssetRef, ResearchDossier
from crewai_invest_reporter.domain.ports import AssetResolver
from crewai_invest_reporter.services.report_writer import ReportWriter
from crewai_invest_reporter.services.research import ResearchCollector

logger = logging.getLogger(__name__)


class Kickoffable(Protocol):
    """What services need from a crew: run it with inputs and get a result with ``.raw``."""

    def kickoff(self, inputs: dict[str, Any]) -> Any: ...


CrewFactory = Callable[[], Kickoffable]
InputsBuilder = Callable[[ResearchDossier, date], dict[str, str]]


class AssetReportService:
    """Generate the report of one asset: resolve → collect (no LLM) → analyze (LLM) → write."""

    def __init__(
        self,
        crew_factory: CrewFactory,
        inputs_builder: InputsBuilder,
        resolver: AssetResolver,
        collector: ResearchCollector,
        writer: ReportWriter,
    ) -> None:
        self._crew_factory = crew_factory
        self._inputs_builder = inputs_builder
        self._resolver = resolver
        self._collector = collector
        self._writer = writer

    def resolve(self, target: str | AssetRef) -> AssetRef:
        """Identify the asset; holdings from Open Finance are enriched with registry data."""
        if isinstance(target, str):
            return self._resolver.resolve(target)
        try:
            resolved = self._resolver.resolve(target.identifier)
        except InvestReporterError as exc:
            logger.info("Could not enrich %s, using holding data as-is: %s", target.identifier, exc)
            return target
        # Keep the class reported by the institution: it is more reliable than conventions.
        return replace(
            target,
            name=target.name or resolved.name,
            cnpj=target.cnpj or resolved.cnpj,
            foreign_ticker=target.foreign_ticker or resolved.foreign_ticker,
        )

    def collect(self, target: str | AssetRef) -> ResearchDossier:
        """Everything the agents will read, gathered without any LLM call."""
        return self._collector.collect(self.resolve(target))

    def generate(self, target: str | AssetRef, today: date | None = None) -> Path:
        today = today or date.today()
        dossier = self.collect(target)
        result = self._crew_factory().kickoff(inputs=self._inputs_builder(dossier, today))
        return self._writer.write(dossier.asset.report_name, result.raw)
