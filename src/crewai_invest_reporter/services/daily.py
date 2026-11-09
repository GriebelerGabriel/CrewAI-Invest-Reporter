"""The day's digests: generated once per day for every watchlist stock, then sent to everyone.

For each stock only what appeared since its previous digest is collected (news and CVM
filings whose links were not reported yet), and one agent writes a short briefing. A stock
with nothing new costs no LLM call.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import date

from crewai_invest_reporter.domain.models import Digest, ResearchDossier, Source, Stock
from crewai_invest_reporter.providers.database import Database
from crewai_invest_reporter.services.research import ResearchCollector

logger = logging.getLogger(__name__)

# News/filings window when the previous digest is old (e.g. after a long holiday).
MAX_WINDOW_DAYS = 7

# (dossier, day, days covered) -> (briefing, cited URLs), or None if nothing relevant.
Summarizer = Callable[[ResearchDossier, date, int], tuple[str, list[str]] | None]


class DailyDigestService:
    def __init__(
        self,
        store: Database,
        resolve: Callable[[str], Stock],
        collector: ResearchCollector,
        summarize: Summarizer,
    ) -> None:
        self._store = store
        self._resolve = resolve
        self._collector = collector
        self._summarize = summarize

    def run(self, day: date, tickers: list[str]) -> list[str]:
        """Build the missing digests of ``day``; returns the tickers that failed."""
        failed = []
        for ticker in tickers:
            if self._store.digest(day, ticker) is not None:
                continue  # already built today: running twice costs nothing
            try:
                self._store.save_digest(self._build(ticker, day))
            except Exception:  # one stock failing must not block the others
                logger.exception("%s: digest failed", ticker)
                failed.append(ticker)
        return failed

    def _build(self, ticker: str, day: date) -> Digest:
        stock = self._resolve(ticker)
        previous = self._store.last_digest(stock.ticker, before=day)
        days = min(max((day - previous.day).days, 1), MAX_WINDOW_DAYS) if previous else 1
        seen = frozenset(s.url for s in previous.sources) if previous else frozenset()
        dossier = self._collector.collect_new(stock, days=days, seen=seen)

        price = dossier.fundamentals.metrics.get("currentPrice") if dossier.fundamentals else None
        change = price / previous.price - 1 if price and previous and previous.price else None
        briefing = None
        if dossier.news or dossier.documents:
            briefing = self._summarize(dossier, day, days)
        summary, cited = briefing or (None, [])

        found = [Source(d.filing.title, d.filing.url, "filing") for d in dossier.documents]
        found += [Source(n.title, n.url) for n in dossier.news]
        by_url = {source.url: source for source in found}
        # Cited sources first, in citation order ([1], [2]...), then the others: every link
        # is kept so the next digest skips what was already seen.
        sources = [replace(by_url[url], cited=True) for url in cited if url in by_url]
        sources += [source for source in found if source.url not in cited]
        return Digest(day, stock.ticker, stock.name, price, change, summary, tuple(sources))
