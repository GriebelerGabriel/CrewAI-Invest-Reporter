"""Abstractions (ports) that the research collector depends on.

The collector depends on these protocols instead of concrete HTTP clients, so tests can
swap in fakes without touching the code that consumes them.
"""

from __future__ import annotations

from typing import Protocol

from crewai_invest_reporter.domain.models import Filing, Fundamentals, NewsItem, Stock


class NewsProvider(Protocol):
    def search(self, query: str, *, max_results: int, days: int) -> list[NewsItem]: ...


class FundamentalsProvider(Protocol):
    def fetch(self, ticker: str) -> Fundamentals: ...


class FilingsProvider(Protocol):
    def list_filings(self, stock: Stock, *, days: int, limit: int) -> list[Filing]: ...


class DocumentReader(Protocol):
    def read(self, url: str) -> str:
        """The text of the document at ``url``."""
        ...
