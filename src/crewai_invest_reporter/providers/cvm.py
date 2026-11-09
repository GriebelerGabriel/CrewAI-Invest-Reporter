"""Official data from CVM's open data portal (https://dados.cvm.gov.br).

* ``CvmOpenDataClient`` reads the yearly zip datasets (cached on disk for a day).
* ``CvmCompanyRegistry`` maps B3 tickers to companies (FCA "valor mobiliário" dataset).
* ``CvmCompanyFilingsProvider`` lists a company's IPE filings: fatos relevantes, comunicados,
  earnings releases ("Dados Econômico-Financeiros"), dividend notices...
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path

from crewai_invest_reporter.domain.cnpj import cnpj_digits
from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import Filing, Stock
from crewai_invest_reporter.providers.cache import DiskCachedFetcher

logger = logging.getLogger(__name__)

BASE_URL = "https://dados.cvm.gov.br/dados"

# Dataset path templates, formatted with the year.
FCA_ZIP = "CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{year}.zip"
FCA_SECURITIES_CSV = "fca_cia_aberta_valor_mobiliario_{year}.csv"
IPE_ZIP = "CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{year}.zip"
IPE_CSV = "ipe_cia_aberta_{year}.csv"

# IPE categories that carry investment-relevant information (others are mostly governance
# paperwork such as insider position reports or committee bylaws).
RELEVANT_IPE_CATEGORIES = frozenset(
    {
        "Fato Relevante",
        "Comunicado ao Mercado",
        "Dados Econômico-Financeiros",
        "Relatório Proventos",
        "Aviso aos Acionistas",
        "Calendário de Eventos Corporativos",
        "Informações de Companhias em Recuperação Judicial ou Extrajudicial",
    }
)


class CvmOpenDataClient:
    def __init__(self, fetcher: DiskCachedFetcher) -> None:
        self._fetcher = fetcher

    @classmethod
    def with_cache(cls, cache_dir: Path, **kwargs) -> CvmOpenDataClient:
        return cls(DiskCachedFetcher(cache_dir / "cvm", **kwargs))

    def iter_rows(self, zip_path: str, member: str) -> Iterator[dict[str, str]]:
        """Yield CSV rows (CVM files are ``;``-separated, latin-1 encoded)."""
        try:
            archive = zipfile.ZipFile(io.BytesIO(self._download(zip_path)))
        except zipfile.BadZipFile as exc:
            raise ProviderError(f"CVM dataset {zip_path} is not a valid zip") from exc
        try:
            handle = archive.open(member)
        except KeyError as exc:
            raise ProviderError(f"CVM dataset {zip_path} has no member {member}") from exc
        with handle:
            yield from csv.DictReader(io.TextIOWrapper(handle, encoding="latin-1"), delimiter=";")

    def iter_rows_for_years(
        self, zip_template: str, member_template: str, years: list[int]
    ) -> Iterator[dict[str, str]]:
        """Rows of several yearly files; a year not published yet (early January) is skipped."""
        loaded = 0
        for year in years:
            try:
                yield from self.iter_rows(
                    zip_template.format(year=year), member_template.format(year=year)
                )
                loaded += 1
            except ProviderError as exc:
                logger.info("Skipping CVM dataset for %s: %s", year, exc)
        if not loaded:
            raise ProviderError(f"No CVM dataset available for {zip_template} in {years}")

    def _download(self, zip_path: str) -> bytes:
        return self._fetcher.get(f"{BASE_URL}/{zip_path}", cache_key=zip_path.replace("/", "__"))


def _recent_years(today: date) -> list[int]:
    return [today.year, today.year - 1]


@dataclass(frozen=True)
class RegistryEntry:
    cnpj: str
    name: str


class CvmCompanyRegistry:
    """B3 ticker -> listed company (CNPJ and legal name)."""

    def __init__(self, client: CvmOpenDataClient, today: Callable[[], date] = date.today) -> None:
        self._client = client
        self._today = today

    def lookup(self, ticker: str) -> RegistryEntry | None:
        return self._by_ticker.get(ticker.upper())

    @cached_property
    def _by_ticker(self) -> dict[str, RegistryEntry]:
        entries: dict[str, RegistryEntry] = {}
        # Oldest year first so the most recent filing wins.
        for row in self._client.iter_rows_for_years(
            FCA_ZIP, FCA_SECURITIES_CSV, sorted(_recent_years(self._today()))
        ):
            ticker = (row.get("Codigo_Negociacao") or "").strip().upper()
            cnpj = cnpj_digits(row.get("CNPJ_Companhia"))
            if ticker and cnpj:
                entries[ticker] = RegistryEntry(cnpj, (row.get("Nome_Empresarial") or "").strip())
        return entries


class CvmCompanyFilingsProvider:
    """IPE filings of a listed company, newest first."""

    SOURCE = "CVM (IPE)"

    def __init__(
        self,
        client: CvmOpenDataClient,
        today: Callable[[], date] = date.today,
        categories: frozenset[str] = RELEVANT_IPE_CATEGORIES,
    ) -> None:
        self._client = client
        self._today = today
        self._categories = categories

    def list_filings(self, stock: Stock, *, days: int = 180, limit: int = 20) -> list[Filing]:
        if not stock.cnpj:
            return []
        since = self._today() - timedelta(days=days)
        filings = [f for f in self._by_cnpj.get(stock.cnpj, []) if f.delivered_on >= since]
        filings.sort(key=lambda f: f.delivered_on, reverse=True)
        return filings[:limit]

    @cached_property
    def _by_cnpj(self) -> dict[str, list[Filing]]:
        """Index relevant filings once: the yearly files hold tens of thousands of rows."""
        index: dict[str, list[Filing]] = {}
        rows = self._client.iter_rows_for_years(IPE_ZIP, IPE_CSV, _recent_years(self._today()))
        for row in rows:
            cnpj = cnpj_digits(row.get("CNPJ_Companhia"))
            if not cnpj or row.get("Categoria") not in self._categories:
                continue
            filing = self._to_filing(row)
            if filing is not None:
                index.setdefault(cnpj, []).append(filing)
        return index

    def _to_filing(self, row: dict[str, str]) -> Filing | None:
        try:
            delivered = date.fromisoformat(row.get("Data_Entrega", ""))
        except ValueError:
            return None
        url = row.get("Link_Download") or ""
        if not url:
            return None
        category = row.get("Categoria", "")
        title = " - ".join(
            part for part in (row.get("Tipo"), row.get("Especie"), row.get("Assunto")) if part
        )
        return Filing(
            title=title or category,
            category=category,
            delivered_on=delivered,
            url=url,
            source=self.SOURCE,
            reference=row.get("Data_Referencia") or None,
        )
