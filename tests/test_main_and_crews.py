from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

import pytest

from crewai_invest_reporter import main
from crewai_invest_reporter.crews.asset_report.crew import (
    AssetReportCrew,
    VerdictRecord,
    asset_report_inputs,
    read_analysis,
)
from crewai_invest_reporter.crews.daily_news.crew import DailyNewsCrew, daily_news_inputs
from crewai_invest_reporter.domain.errors import InvalidTickerError
from crewai_invest_reporter.domain.models import (
    DocumentExcerpt,
    Filing,
    Fundamentals,
    NewsItem,
    ResearchDossier,
    Stock,
    Verdict,
)


@pytest.fixture
def fake_llm_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("BIFROST_BASE_URL", "")  # also shadows a local .env


def _dossier():
    filing = Filing("Fato Relevante", "Fato Relevante", date(2026, 10, 2), "https://f/1", "CVM")
    return ResearchDossier(
        stock=Stock("PETR4", "PETROBRAS", "33000167000101"),
        news=(NewsItem("PETR4 paga dividendos {x}", "Valor", "hoje", "https://n/1"),),
        fundamentals=Fundamentals(
            "PETR4", "https://i10", {"dividendYield": 0.12, "currentPrice": 38.45}, {}
        ),
        documents=(DocumentExcerpt(filing, text='Lucro de R$ 32 bi {"json": true}'),),
        gaps=("Notícias: poucas",),
    )


def test_invalid_ticker_fails_cleanly(capsys):
    assert main.main(["dossier", "../x"]) == 1
    assert "Invalid B3 ticker" in capsys.readouterr().err


def test_report_without_watchlist_fails_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("WATCHLIST_FILE", str(tmp_path / "missing.txt"))
    assert main.main(["report"]) == 1
    assert "missing.txt not found" in capsys.readouterr().err


class _FakeService:
    def __init__(self, fail=()):
        self.fail, self.calls = fail, []

    def generate(self, ticker):
        self.calls.append(ticker)
        if ticker in self.fail:
            raise self.fail[ticker]
        return f"reports/{ticker}_investment_report.md"


def test_report_runs_the_whole_watchlist_and_keeps_going_after_failures(
    monkeypatch, tmp_path, capsys, fake_llm_key
):
    watchlist = tmp_path / "watchlist.txt"
    watchlist.write_text("PETR4\nXPTO3\nVALE3\n", encoding="utf-8")
    monkeypatch.setenv("WATCHLIST_FILE", str(watchlist))
    service = _FakeService(
        fail={"XPTO3": InvalidTickerError("XPTO3 is not listed"), "VALE3": RuntimeError("llm")}
    )
    monkeypatch.setattr(main.Container, "report_service", service)

    assert main.main(["report"]) == 1
    assert service.calls == ["PETR4", "XPTO3", "VALE3"]
    out, err = capsys.readouterr()
    assert "PETR4: report written to" in out
    assert "XPTO3: error: XPTO3 is not listed" in err
    assert "Failed: XPTO3, VALE3" in err


def test_report_with_tickers_ignores_the_watchlist(monkeypatch, tmp_path, fake_llm_key):
    monkeypatch.setenv("WATCHLIST_FILE", str(tmp_path / "missing.txt"))
    service = _FakeService()
    monkeypatch.setattr(main.Container, "report_service", service)
    assert main.main(["report", "BBAS3"]) == 0
    assert service.calls == ["BBAS3"]


def test_crew_has_no_tools(fake_llm_key):
    crew = AssetReportCrew(verbose=False).crew()
    assert [t.name for t in crew.tasks] == [
        "filings_analysis_task",
        "news_synthesis_task",
        "fundamentals_task",
        "investment_rating_task",
        "verdict_task",
    ]
    assert all(not agent.tools for agent in crew.agents)
    assert crew.tasks[-1].output_pydantic is VerdictRecord


def test_inputs_interpolate_every_task(fake_llm_key):
    inputs = asset_report_inputs(_dossier(), date(2026, 10, 17))
    assert inputs["company"] == "PETROBRAS"
    assert "Lucro de R$ 32 bi" in inputs["official_documents"]
    assert "dividendYield" in inputs["fundamentals_data"]

    crew = AssetReportCrew(verbose=False).crew()
    placeholder = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
    for crew_task in crew.tasks:
        crew_task.interpolate_inputs_and_add_conversation_history(inputs)
        unresolved = placeholder.findall(crew_task.description.replace("{x}", ""))
        assert not unresolved, (crew_task.name, unresolved)
    assert "Lucro de R$ 32 bi" in crew.tasks[0].description


def test_format_dossier_shows_documents_and_gaps():
    text = main._format_dossier(_dossier())
    assert text.startswith("PETR4 (PETROBRAS) | CNPJ 33000167000101")
    assert "Fato Relevante" in text
    assert "! Notícias: poucas" in text


def test_read_analysis_returns_the_report_and_the_parsed_verdict():
    record = VerdictRecord(
        rating="Good to invest", probability_good=65, probability_loss=35, confidence="média"
    )
    outputs = [
        SimpleNamespace(name="investment_rating_task", raw="```markdown\n# Relatório\n```"),
        SimpleNamespace(name="verdict_task", raw="{}", pydantic=record),
    ]
    analysis = read_analysis(outputs)
    assert analysis.report == "# Relatório"
    assert analysis.verdict == Verdict("Good to invest", 65, 35, "média")

    unparsed = read_analysis([outputs[0], SimpleNamespace(name="verdict_task", pydantic=None)])
    assert (unparsed.report, unparsed.verdict) == ("# Relatório", None)


def test_daily_news_inputs_interpolate_the_task(fake_llm_key):
    inputs = daily_news_inputs(_dossier(), date(2026, 11, 9), days=3, price_change=-0.0123)
    assert inputs["price_line"] == "R$ 38.45 (-1.23% desde o resumo anterior)"
    assert inputs["days"] == "3"

    crew = DailyNewsCrew().crew()
    placeholder = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
    for crew_task in crew.tasks:
        crew_task.interpolate_inputs_and_add_conversation_history(inputs)
        unresolved = placeholder.findall(crew_task.description.replace("{x}", ""))
        assert not unresolved, (crew_task.name, unresolved)
    assert all(not agent.tools for agent in crew.agents)
