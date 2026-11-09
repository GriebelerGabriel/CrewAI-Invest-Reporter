"""Reusable fakes implementing the domain ports, and sample files."""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook

from crewai_invest_reporter.domain.models import Document, Filing, Fundamentals, NewsItem

# Layout of B3's "posição" export (Área do Investidor): one tab per product.
B3_STOCK_HEADER = [
    "Produto", "Instituição", "Conta", "Código de Negociação", "CNPJ da Empresa",
    "Código ISIN / Distribuição", "Tipo", "Escriturador", "Quantidade",
    "Quantidade Disponível", "Quantidade Indisponível", "Motivo", "Preço de Fechamento",
    "Valor Atualizado",
]  # fmt: skip


def b3_stock_row(ticker: str, broker: str, quantity) -> list:
    return [
        f"{ticker} - EMPRESA", broker, "123", ticker, "33.000.167/0001-01", "BRPETRACNPR6",
        "PN", "BANCO", quantity, quantity, 0, "-", 10.0, 1000.0,
    ]  # fmt: skip


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    """An .xlsx file with one tab per entry of ``sheets`` (tab name -> rows)."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def b3_position_file() -> bytes:
    """PETR4 at two brokers, an FII, a Tesouro Direto tab to skip and a "Total" row."""
    total = [""] * 12 + ["Total", 9999.0]
    return xlsx(
        {
            "Acoes": [
                B3_STOCK_HEADER,
                b3_stock_row("PETR4", "XP INVESTIMENTOS", 100),
                b3_stock_row("PETR4", "NU INVEST", 50),
                total,
            ],
            "Fundo de Investimento": [
                [
                    "Produto",
                    "Instituição",
                    "Conta",
                    "Código de Negociação ",
                    "CNPJ do Fundo",
                    "Código ISIN / Distribuição",
                    "Tipo",
                    "Administrador",
                    "Quantidade",
                ],
                [
                    "MXRF11 - MAXI RENDA",
                    "NU INVEST",
                    "456",
                    "MXRF11",
                    "97.521.225/0001-25",
                    "BRMXRFCTF008",
                    "Cotas",
                    "BTG",
                    "1.000",
                ],
            ],
            "Tesouro Direto": [
                ["Produto", "Instituição", "Código ISIN", "Indexador", "Vencimento", "Quantidade"],
                ["Tesouro Selic 2029", "NU INVEST", "BRSTNCLF1RU6", "SELIC", "01/03/2029", 1.5],
            ],
        }
    )


class FakeFilings:
    def __init__(self, filings=None, error=None):
        self.filings, self.error, self.calls = filings, error, []

    def list_filings(self, asset, *, days=180, limit=20):
        self.calls.append((asset.identifier, days, limit))
        if self.error:
            raise self.error
        if self.filings is not None:
            return self.filings
        return [
            Filing(
                "Relatório Gerencial",
                "Relatórios",
                date(2026, 10, 2),
                "https://fnet/doc?id=1",
                "B3 FundosNET",
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
