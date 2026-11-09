"""Abstractions (ports) that the application depends on.

Services depend on these protocols instead of concrete HTTP clients, so a data source can be
swapped (or faked in tests) without touching the code that consumes it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from crewai_invest_reporter.domain.models import Document, Filing, Fundamentals, NewsItem, Stock


@runtime_checkable
class NewsProvider(Protocol):
    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        days: int = 30,
        language: str = "pt-BR",
        region: str = "BR",
    ) -> list[NewsItem]: ...


@runtime_checkable
class FundamentalsProvider(Protocol):
    def fetch(self, ticker: str) -> Fundamentals: ...


@runtime_checkable
class FilingsProvider(Protocol):
    def list_filings(self, stock: Stock, *, days: int = 180, limit: int = 20) -> list[Filing]: ...


@runtime_checkable
class DocumentReader(Protocol):
    def read(self, url: str) -> Document: ...
