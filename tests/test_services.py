from __future__ import annotations

from datetime import date

import pytest

from crewai_invest_reporter.crews import strip_code_fence
from crewai_invest_reporter.domain.errors import InvalidTickerError, ProviderError
from crewai_invest_reporter.domain.models import (
    Analysis,
    Fundamentals,
    ResearchDossier,
    Verdict,
)
from crewai_invest_reporter.providers.cvm import RegistryEntry
from crewai_invest_reporter.providers.database import Database
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


def _ratings(db):
    with db._connect() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM ratings")]


def _service(tmp_path, analysis=ANALYSIS, collector=None):
    calls = []

    def analyze(dossier, today):
        calls.append((dossier.stock.ticker, today))
        return analysis

    service = AssetReportService(
        analyze=analyze,
        resolve=StockResolver(FakeRegistry()).resolve,
        collector=collector or FakeCollector(),
        reports_dir=tmp_path / "reports",
        ratings=Database(tmp_path / "r.db"),
        model="openai/gpt-4.1-mini",
    )
    return service, calls, service._ratings


def test_strip_code_fence():
    assert strip_code_fence("```markdown\n# Título\n```") == "# Título"
    assert strip_code_fence("  # Título  ") == "# Título"


def test_generate_writes_the_report_and_saves_the_rating(tmp_path):
    collector = FakeCollector()
    service, calls, db = _service(tmp_path, collector=collector)
    path = service.generate("petr4.sa", TODAY)

    assert path == tmp_path / "reports" / "PETR4_investment_report.md"
    assert path.read_text(encoding="utf-8") == "# Relatório\n"
    assert calls == [("PETR4", TODAY)]
    assert [s.cnpj for s in collector.stocks] == ["33000167000101"]
    (row,) = _ratings(db)
    assert row["day"] == "2026-10-17" and row["ticker"] == "PETR4"
    assert (row["rating"], row["probability_good"], row["probability_loss"]) == (
        "Good to invest",
        65,
        35,
    )
    assert row["price"] == 38.45 and '"trailingPE": 4.5' in row["metrics"]
    assert (row["model"], row["report"]) == ("openai/gpt-4.1-mini", "# Relatório")


def test_every_run_is_kept(tmp_path):
    service, _, db = _service(tmp_path)
    service.generate("PETR4", TODAY)
    service.generate("PETR4", TODAY)
    assert len(_ratings(db)) == 2


def test_report_without_verdict_is_written_but_not_rated(tmp_path):
    service, _, db = _service(tmp_path, analysis=Analysis("# Relatório"))
    assert service.generate("PETR4", TODAY).exists()
    assert _ratings(db) == []


def test_resolver_rejects_invalid_and_unlisted_tickers():
    resolver = StockResolver(FakeRegistry())
    with pytest.raises(InvalidTickerError, match="Invalid B3 ticker"):
        resolver.resolve("../../etc/passwd")
    with pytest.raises(InvalidTickerError, match="not a company listed"):
        resolver.resolve("XPTO3")


def test_resolver_works_without_cnpj_when_cvm_is_offline():
    stock = StockResolver(FakeRegistry(error=ProviderError("CVM offline"))).resolve("PETR4")
    assert (stock.ticker, stock.name, stock.cnpj) == ("PETR4", None, None)
