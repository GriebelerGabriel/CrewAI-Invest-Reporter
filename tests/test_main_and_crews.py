from __future__ import annotations

from datetime import date

import pytest

from crewai_invest_reporter import main


def test_format_holdings(sample_portfolio):
    table = main._format_holdings(sample_portfolio)
    assert table.splitlines()[1].startswith("PETR4")
    assert "5,300.00" in table.splitlines()[-1]


def test_holdings_from_pluggy_without_credentials_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setenv("WALLET_SOURCE", "pluggy")
    for var in ("PLUGGY_CLIENT_ID", "PLUGGY_CLIENT_SECRET", "PLUGGY_ITEM_IDS"):
        monkeypatch.setenv(var, "")
    assert main.main(["holdings"]) == 1
    assert "Open Finance is not configured" in capsys.readouterr().err


def test_holdings_from_an_empty_wallet_points_to_the_page(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WALLET_SOURCE", "manual")
    monkeypatch.setenv("WALLET_FILE", str(tmp_path / "wallet.json"))
    assert main.main(["holdings"]) == 0
    assert "wallet page" in capsys.readouterr().out


def test_unknown_wallet_source_fails_cleanly(monkeypatch, capsys):
    monkeypatch.setenv("WALLET_SOURCE", "excel")
    assert main.main(["holdings"]) == 1
    assert "WALLET_SOURCE must be one of" in capsys.readouterr().err


def test_invalid_identifier_fails_cleanly(capsys):
    assert main.main(["asset", "../x"]) == 1
    assert "Not a B3 ticker nor a CNPJ" in capsys.readouterr().err


@pytest.fixture
def fake_llm_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def _dossier():
    from crewai_invest_reporter.domain.models import (
        AssetClass,
        AssetRef,
        DocumentExcerpt,
        Filing,
        Fundamentals,
        NewsItem,
        ResearchDossier,
    )

    asset = AssetRef("MXRF11", AssetClass.REAL_ESTATE_FUND, "Maxi Renda", "97521225000125")
    filing = Filing("Relatório Gerencial", "Relatórios", date(2026, 10, 2), "https://f/1", "FNET")
    return ResearchDossier(
        asset=asset,
        news=(NewsItem("MXRF11 paga dividendos {x}", "Valor", "hoje", "https://n/1"),),
        fundamentals=Fundamentals("MXRF11", "https://i10", {"dividendYield": 0.12}, {}),
        documents=(DocumentExcerpt(filing, text='Vacância de 0% {"json": true}'),),
        gaps=("Notícias: poucas",),
    )


def test_asset_crew_has_no_tools(fake_llm_key):
    from crewai_invest_reporter.crews.asset_report.crew import AssetReportCrew

    crew = AssetReportCrew(verbose=False).crew()
    assert [t.name for t in crew.tasks] == [
        "filings_analysis_task",
        "news_synthesis_task",
        "fundamentals_task",
        "investment_rating_task",
    ]
    assert all(not agent.tools for agent in crew.agents)


def test_asset_report_inputs_interpolate_every_task(fake_llm_key):
    import re

    from crewai_invest_reporter.crews.asset_report.crew import AssetReportCrew, asset_report_inputs

    inputs = asset_report_inputs(_dossier(), date(2026, 10, 17))
    assert "P/L does not apply" in inputs["analysis_guidelines"]
    assert inputs["asset_class"] == "Fundo Imobiliário (FII)"
    assert "Vacância de 0%" in inputs["official_documents"]
    assert "dividendYield" in inputs["fundamentals_data"]

    crew = AssetReportCrew(verbose=False).crew()
    placeholder = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
    for crew_task in crew.tasks:
        crew_task.interpolate_inputs_and_add_conversation_history(inputs)
        unresolved = placeholder.findall(crew_task.description.replace("{x}", ""))
        assert not unresolved, (crew_task.name, unresolved)
    assert "Vacância de 0%" in crew.tasks[0].description


def test_format_dossier_shows_documents_and_gaps():
    text = main._format_dossier(_dossier())
    assert "REAL_ESTATE_FUND" in text
    assert "Relatório Gerencial" in text
    assert "! Notícias: poucas" in text
