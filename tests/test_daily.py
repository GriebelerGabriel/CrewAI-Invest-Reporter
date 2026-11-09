from __future__ import annotations

from datetime import date

import pytest

from crewai_invest_reporter.domain.models import (
    DocumentExcerpt,
    Filing,
    Fundamentals,
    NewsItem,
    ResearchDossier,
    Stock,
)
from crewai_invest_reporter.providers.database import Database
from crewai_invest_reporter.services.daily import DailyDigestService

FRI, MON = date(2026, 11, 6), date(2026, 11, 9)


class FakeCollector:
    def __init__(self, news=(), documents=(), price=10.0):
        self.news, self.documents, self.price, self.calls = news, documents, price, []

    def collect_new(self, stock, *, days, seen=frozenset()):
        self.calls.append((stock.ticker, days, seen))
        if stock.ticker == "BOOM3":
            raise RuntimeError("site down")
        return ResearchDossier(
            stock=stock,
            news=tuple(n for n in self.news if n.url not in seen),
            fundamentals=Fundamentals(stock.ticker, "u", {"currentPrice": self.price}, {}),
            documents=tuple(self.documents),
        )


class FakeSummarizer:
    """Cites the last item (a news link) first, then the first one (a filing, if any)."""

    def __init__(self, nothing_relevant=False):
        self.nothing_relevant, self.calls = nothing_relevant, []

    def __call__(self, dossier, day, days):
        self.calls.append((dossier.stock.ticker, day, days))
        if self.nothing_relevant:
            return None
        urls = [d.filing.url for d in dossier.documents] + [n.url for n in dossier.news]
        return "- PETR4 anunciou dividendos. [1, 2]", list(dict.fromkeys([urls[-1], urls[0]]))


def _service(tmp_path, collector, summarizer=None):
    db = Database(tmp_path / "reporter.db")
    service = DailyDigestService(
        store=db,
        resolve=lambda ticker: Stock(ticker, f"Empresa {ticker}", "33000167000101"),
        collector=collector,
        summarize=summarizer or FakeSummarizer(),
    )
    return service, db


NEWS = (NewsItem("Petrobras paga dividendos", "Valor", "", "https://n/1"),)
FILING = Filing("Fato Relevante", "Fato Relevante", MON, "https://rad.cvm/1", "CVM (IPE)")


def test_builds_digest_with_summary_sources_and_price(tmp_path):
    collector, summarizer = (
        FakeCollector(NEWS, [DocumentExcerpt(FILING, text="x")]),
        FakeSummarizer(),
    )
    service, db = _service(tmp_path, collector, summarizer)
    assert service.run(MON, ["PETR4"]) == []

    digest = db.digest(MON, "PETR4")
    assert digest.summary == "- PETR4 anunciou dividendos. [1, 2]"
    # Cited sources come first, in citation order, and are marked as cited.
    assert [(s.kind, s.url, s.cited) for s in digest.sources] == [
        ("news", "https://n/1", True),
        ("filing", "https://rad.cvm/1", True),
    ]
    assert (digest.name, digest.price, digest.price_change) == ("Empresa PETR4", 10.0, None)
    assert collector.calls == [("PETR4", 1, frozenset())]  # first digest: last day only


def test_next_digest_covers_the_gap_skips_reported_links_and_computes_the_move(tmp_path):
    collector = FakeCollector(NEWS)
    service, db = _service(tmp_path, collector)
    service.run(FRI, ["PETR4"])
    collector.price = 11.0
    summarizer = FakeSummarizer()
    service._summarize = summarizer

    service.run(MON, ["PETR4"])
    _, days, seen = collector.calls[-1]
    assert (days, seen) == (3, frozenset({"https://n/1"}))  # Friday -> Monday
    monday = db.digest(MON, "PETR4")
    assert monday.price_change == pytest.approx(0.1)
    assert monday.summary is None  # the only news was already sent on Friday
    assert summarizer.calls == []  # nothing new: no LLM call


def test_nothing_relevant_from_the_agent_leaves_no_summary(tmp_path):
    service, db = _service(tmp_path, FakeCollector(NEWS), FakeSummarizer(nothing_relevant=True))
    service.run(MON, ["PETR4"])
    digest = db.digest(MON, "PETR4")
    assert digest.summary is None
    # Seen but not cited: kept, so tomorrow skips it, but not shown in the email.
    assert [(s.url, s.cited) for s in digest.sources] == [("https://n/1", False)]


def test_run_is_idempotent_and_one_failure_does_not_stop_the_others(tmp_path):
    collector, summarizer = FakeCollector(NEWS), FakeSummarizer()
    service, db = _service(tmp_path, collector, summarizer)
    assert service.run(MON, ["BOOM3", "PETR4"]) == ["BOOM3"]
    assert service.run(MON, ["BOOM3", "PETR4"]) == ["BOOM3"]  # PETR4 is not rebuilt
    assert len(summarizer.calls) == 1
    assert [d.ticker for d in db.digests_for(MON)] == ["PETR4"]
