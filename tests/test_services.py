from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from crewai_invest_reporter.domain.errors import InvalidTickerError
from crewai_invest_reporter.domain.models import AssetClass, AssetRef, ResearchDossier
from crewai_invest_reporter.domain.tickers import normalize_ticker
from crewai_invest_reporter.services.report_writer import ReportWriter
from crewai_invest_reporter.services.reporting import AssetReportService

TODAY = date(2026, 10, 17)


class FakeCrew:
    def __init__(self, output="# Relatório", fail_for=()):
        self.output, self.fail_for, self.inputs = output, set(fail_for), []

    def kickoff(self, inputs):
        self.inputs.append(inputs)
        if inputs.get("asset_id") in self.fail_for:
            raise RuntimeError("llm down")
        return SimpleNamespace(raw=self.output)


class FakeResolver:
    def __init__(self):
        self.calls = []

    def resolve(self, identifier):
        self.calls.append(identifier)
        ticker = normalize_ticker(identifier)
        return AssetRef(ticker, AssetClass.STOCK, f"Empresa {ticker}", "33000167000101")


class FakeCollector:
    def __init__(self):
        self.assets = []

    def collect(self, asset):
        self.assets.append(asset)
        return ResearchDossier(asset=asset, gaps=("sem notícias",))


def _inputs(dossier, today):
    asset = dossier.asset
    return {"asset_id": asset.identifier, "asset_class": asset.asset_class.value}


def _asset_service(crew, writer, resolver=None, collector=None):
    return AssetReportService(
        crew_factory=lambda: crew,
        inputs_builder=_inputs,
        resolver=resolver or FakeResolver(),
        collector=collector or FakeCollector(),
        writer=writer,
    )


def test_report_writer_strips_code_fence_and_rejects_unsafe_names(tmp_path):
    writer = ReportWriter(tmp_path / "reports")
    path = writer.write("PETR4_report", "```markdown\n# Título\n```")
    assert path.read_text(encoding="utf-8") == "# Título\n"
    with pytest.raises(ValueError):
        writer.path_for("../escape")


def test_asset_service_resolves_identifier_and_writes_report(tmp_path):
    crew = FakeCrew()
    path = _asset_service(crew, ReportWriter(tmp_path)).generate("petr4.sa", today=TODAY)
    assert path == tmp_path / "PETR4_investment_report.md"
    assert crew.inputs == [{"asset_id": "PETR4", "asset_class": "STOCK"}]


def test_asset_service_rejects_invalid_identifier(tmp_path):
    with pytest.raises(InvalidTickerError):
        _asset_service(FakeCrew(), ReportWriter(tmp_path)).generate("../../etc/passwd")


def test_asset_service_enriches_holdings_but_keeps_their_asset_class(tmp_path):
    resolver = FakeResolver()
    service = _asset_service(FakeCrew(), ReportWriter(tmp_path), resolver)
    enriched = service.resolve(AssetRef("BOVA11", AssetClass.ETF))
    assert enriched.asset_class is AssetClass.ETF
    assert enriched.cnpj == "33000167000101"
    assert enriched.name == "Empresa BOVA11"

    assert resolver.calls == ["BOVA11"]


def test_asset_service_collects_before_running_the_crew(tmp_path):
    collector, crew = FakeCollector(), FakeCrew()
    service = _asset_service(crew, ReportWriter(tmp_path), collector=collector)
    service.generate("VALE3", today=TODAY)
    assert [a.identifier for a in collector.assets] == ["VALE3"]
    assert crew.inputs == [{"asset_id": "VALE3", "asset_class": "STOCK"}]


def test_fund_reports_are_named_by_cnpj(tmp_path):
    fund = AssetRef("11222333000181", AssetClass.MUTUAL_FUND, "Fundo", "11222333000181")
    path = _asset_service(FakeCrew(), ReportWriter(tmp_path)).generate(fund, today=TODAY)
    assert path.name == "FUNDO_11222333000181_investment_report.md"
