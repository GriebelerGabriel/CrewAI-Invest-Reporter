from __future__ import annotations

from datetime import date

import pytest

from crewai_invest_reporter.domain.errors import InvalidTickerError, ProviderError
from crewai_invest_reporter.domain.models import (
    Analysis,
    Fundamentals,
    ResearchDossier,
    Verdict,
)
from crewai_invest_reporter.providers.cvm import RegistryEntry
from crewai_invest_reporter.services.report_writer import ReportWriter
from crewai_invest_reporter.services.reporting import AssetReportService
from crewai_invest_reporter.services.stocks import StockResolver

TODAY = date(2026, 10, 17)
VERDICT = Verdict("Good to invest", 65, 35, "média")
ANALYSIS = Analysis("# Relatório", VERDICT)


class FakeRegistry:
    def __init__(self, error=None):
        self.error = error

    def lookup(self, ticker):
        if self.error:
            raise self.error
        return {"PETR4": RegistryEntry("33000167000101", "PETROBRAS")}.get(ticker)


class FakeCollector:
    def __init__(self):
        self.stocks = []

    def collect(self, stock):
        self.stocks.append(stock)
        fundamentals = Fundamentals(
            stock.ticker, "u", {"currentPrice": 38.45, "trailingPE": 4.5}, {}
        )
        return ResearchDossier(stock=stock, fundamentals=fundamentals)


class FakeRatings:
    def __init__(self):
        self.saved = []

    def save_rating(self, rating):
        self.saved.append(rating)


def _service(tmp_path, analysis=ANALYSIS, ratings=None, collector=None):
    calls = []

    def analyze(dossier, today):
        calls.append((dossier.stock.ticker, today))
        return analysis

    service = AssetReportService(
        analyze=analyze,
        resolve=StockResolver(FakeRegistry()).resolve,
        collector=collector or FakeCollector(),
        writer=ReportWriter(tmp_path),
        ratings=ratings,
        model="openai/gpt-4.1-mini",
    )
    return service, calls


def test_report_writer_strips_code_fence_and_rejects_unsafe_names(tmp_path):
    writer = ReportWriter(tmp_path / "reports")
    path = writer.write("PETR4_report", "```markdown\n# Título\n```")
    assert path.read_text(encoding="utf-8") == "# Título\n"
    with pytest.raises(ValueError):
        writer.path_for("../escape")


def test_generate_writes_the_report_and_saves_the_rating(tmp_path):
    ratings, collector = FakeRatings(), FakeCollector()
    service, calls = _service(tmp_path, ratings=ratings, collector=collector)
    path = service.generate("petr4.sa", today=TODAY)

    assert path == tmp_path / "PETR4_investment_report.md"
    assert path.read_text(encoding="utf-8") == "# Relatório\n"
    assert calls == [("PETR4", TODAY)]
    assert [s.cnpj for s in collector.stocks] == ["33000167000101"]
    (rating,) = ratings.saved
    assert (rating.day, rating.ticker, rating.verdict, rating.price) == (
        TODAY,
        "PETR4",
        VERDICT,
        38.45,
    )
    assert rating.metrics["trailingPE"] == 4.5
    assert rating.model == "openai/gpt-4.1-mini"
    assert rating.report == "# Relatório"


def test_report_without_verdict_is_written_but_not_rated(tmp_path):
    ratings = FakeRatings()
    service, _ = _service(tmp_path, analysis=Analysis("# Relatório"), ratings=ratings)
    assert service.generate("PETR4", today=TODAY).exists()
    assert ratings.saved == []


def test_resolver_rejects_invalid_and_unlisted_tickers():
    resolver = StockResolver(FakeRegistry())
    with pytest.raises(InvalidTickerError, match="Invalid B3 ticker"):
        resolver.resolve("../../etc/passwd")
    with pytest.raises(InvalidTickerError, match="not a company listed"):
        resolver.resolve("XPTO3")


def test_resolver_works_without_cnpj_when_cvm_is_offline():
    stock = StockResolver(FakeRegistry(error=ProviderError("CVM offline"))).resolve("PETR4")
    assert (stock.ticker, stock.name, stock.cnpj) == ("PETR4", None, None)
