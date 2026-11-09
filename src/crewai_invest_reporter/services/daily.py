"""The day's digests: generated once per day for every watchlist stock, then sent to everyone.

For each stock only what appeared since its previous digest is collected (news and CVM
filings whose links were not reported yet), and one agent writes a short briefing. A stock
with nothing new costs no LLM call.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Protocol

from crewai_invest_reporter.domain.models import Digest, ResearchDossier, Source, Stock
from crewai_invest_reporter.services.research import ResearchCollector

logger = logging.getLogger(__name__)

# News/filings window when the previous digest is old (e.g. after a long holiday).
MAX_WINDOW_DAYS = 7

Summarizer = Callable[[ResearchDossier, date, int, "float | None"], "str | None"]


class DigestStore(Protocol):
    def digest(self, day: date, ticker: str) -> Digest | None: ...
    def last_digest(self, ticker: str, before: date) -> Digest | None: ...
    def save_digest(self, digest: Digest) -> None: ...


class DailyDigestService:
    def __init__(
        self,
        store: DigestStore,
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
                self._store.save_digest(self.build(ticker, day))
            except Exception:  # one stock failing must not block the others
                logger.exception("%s: digest failed", ticker)
                failed.append(ticker)
        return failed

    def build(self, ticker: str, day: date) -> Digest:
        stock = self._resolve(ticker)
        previous = self._store.last_digest(stock.ticker, before=day)
        days = min(max((day - previous.day).days, 1), MAX_WINDOW_DAYS) if previous else 1
        seen = frozenset(s.url for s in previous.sources) if previous else frozenset()
        dossier = self._collector.collect_new(stock, days=days, seen=seen)

        price = dossier.fundamentals.metrics.get("currentPrice") if dossier.fundamentals else None
        change = price / previous.price - 1 if price and previous and previous.price else None
        summary = None
        if dossier.news or dossier.documents:
            summary = self._summarize(dossier, day, days, change)
        # Every reported link is kept (not only those shown) so tomorrow skips them.
        sources = tuple(Source(d.filing.title, d.filing.url, "filing") for d in dossier.documents)
        sources += tuple(Source(n.title, n.url) for n in dossier.news)
        return Digest(day, stock.ticker, stock.name, price, change, summary, sources)
