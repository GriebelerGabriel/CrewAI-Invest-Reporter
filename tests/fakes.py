"""Reusable fakes implementing the domain ports."""

from __future__ import annotations

from datetime import date

from crewai_invest_reporter.domain.models import Document, Filing, Fundamentals, NewsItem


class FakeFilings:
    def __init__(self, filings=None, error=None):
        self.filings, self.error, self.calls = filings, error, []

    def list_filings(self, stock, *, days=180, limit=20):
        self.calls.append((stock.ticker, days, limit))
        if self.error:
            raise self.error
        if self.filings is not None:
            return self.filings
        return [
            Filing(
                "Fato Relevante - Dividendos",
                "Fato Relevante",
                date(2026, 10, 2),
                "https://rad.cvm/doc?id=1",
                "CVM (IPE)",
            )
        ]


class FakeDocuments:
    """Returns ``text`` if given, else text unique to each URL repeated ``words`` times."""

    def __init__(self, text=None, error=None, words=10):
        self.text, self.error, self.words, self.calls = text, error, words, []

    def read(self, url):
        self.calls.append(url)
        if self.error:
            raise self.error
        text = self.text if self.text is not None else f"conteúdo de {url} " * self.words
        return Document(url=url, content_type="application/pdf", text=text, pages=3)


class FakeNews:
    def __init__(self, items=None, error=None):
        self.items, self.error, self.calls = items, error, []

    def search(self, query, *, max_results=10, days=30, language="pt-BR", region="BR"):
        self.calls.append((query, max_results, days))
        if self.error:
            raise self.error
        if self.items is not None:
            return self.items
        return [NewsItem(f"Notícia sobre {query}", "Valor", "hoje", f"https://news/{query}")]


class FakeFundamentals:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    def fetch(self, ticker):
        self.calls.append(ticker)
        if self.error:
            raise self.error
        return Fundamentals(ticker=ticker, source_url="u", metrics={"trailingPE": 4.5}, raw={})
