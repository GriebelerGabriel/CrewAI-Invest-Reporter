"""CVM open data: company registry and filings."""

from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import date

import pytest
import responses

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import Stock
from crewai_invest_reporter.providers import cvm
from crewai_invest_reporter.providers.cvm import (
    CvmCompanyFilingsProvider,
    CvmCompanyRegistry,
    CvmOpenDataClient,
    RegistryEntry,
)
from crewai_invest_reporter.providers.http import build_session

TODAY = date(2026, 10, 17)
PETRO = "33.000.167/0001-01"


def _zip(member: str, rows: list[dict[str, str]]) -> bytes:
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=list(rows[0]), delimiter=";")
    writer.writeheader()
    writer.writerows(rows)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, text.getvalue().encode("latin-1"))
    return buffer.getvalue()


def _url(template: str, year: int) -> str:
    return f"{cvm.BASE_URL}/{template.format(year=year)}"


def _client(tmp_path) -> CvmOpenDataClient:
    return CvmOpenDataClient.with_cache(tmp_path, session=build_session(retries=0))


def _mock_fca():
    rows = [
        {"CNPJ_Companhia": PETRO, "Nome_Empresarial": "PETROBRAS", "Codigo_Negociacao": "PETR4"},
        {
            "CNPJ_Companhia": "07.859.971/0001-30",
            "Nome_Empresarial": "TAESA",
            "Codigo_Negociacao": "TAEE11",
        },
    ]
    responses.get(_url(cvm.FCA_ZIP, 2025), status=404)
    responses.get(
        _url(cvm.FCA_ZIP, 2026), body=_zip(cvm.FCA_SECURITIES_CSV.format(year=2026), rows)
    )


def _ipe_row(delivered: str, category: str, subject: str, cnpj: str = PETRO) -> dict[str, str]:
    return {
        "CNPJ_Companhia": cnpj,
        "Categoria": category,
        "Tipo": "",
        "Especie": "",
        "Assunto": subject,
        "Data_Entrega": delivered,
        "Data_Referencia": delivered,
        "Link_Download": f"https://www.rad.cvm.gov.br/doc?{subject}",
    }


@responses.activate
def test_registry_maps_tickers_to_companies(tmp_path):
    _mock_fca()
    companies = CvmCompanyRegistry(_client(tmp_path), today=lambda: TODAY)
    assert companies.lookup("petr4") == RegistryEntry("33000167000101", "PETROBRAS")
    assert companies.lookup("TAEE11") == RegistryEntry("07859971000130", "TAESA")  # a unit
    assert companies.lookup("VALE3") is None


@responses.activate
def test_client_caches_downloads_on_disk(tmp_path):
    _mock_fca()
    CvmCompanyRegistry(_client(tmp_path), today=lambda: TODAY).lookup("PETR4")
    CvmCompanyRegistry(_client(tmp_path), today=lambda: TODAY).lookup("PETR4")
    downloads = [c for c in responses.calls if c.request.url.endswith("2026.zip")]
    assert len(downloads) == 1


@responses.activate
def test_client_uses_stale_cache_when_cvm_is_down(tmp_path):
    _mock_fca()
    CvmCompanyRegistry(_client(tmp_path), today=lambda: TODAY).lookup("PETR4")
    for cached in (tmp_path / "cvm").iterdir():
        os.utime(cached, (0, 0))  # make the cache entry older than the TTL
    responses.replace(responses.GET, _url(cvm.FCA_ZIP, 2026), status=503)
    entry = CvmCompanyRegistry(_client(tmp_path), today=lambda: TODAY).lookup("PETR4")
    assert entry.cnpj == "33000167000101"


@responses.activate
def test_client_raises_when_no_year_is_available(tmp_path):
    responses.get(_url(cvm.FCA_ZIP, 2025), status=404)
    responses.get(_url(cvm.FCA_ZIP, 2026), status=404)
    with pytest.raises(ProviderError, match="No CVM dataset"):
        CvmCompanyRegistry(_client(tmp_path), today=lambda: TODAY).lookup("PETR4")


@responses.activate
def test_company_filings_filter_by_cnpj_category_and_date(tmp_path):
    rows = [
        _ipe_row("2026-10-08", "Fato Relevante", "fato"),
        _ipe_row("2026-09-30", "Comunicado ao Mercado", "comunicado"),
        _ipe_row("2026-10-01", "Reunião da Administração", "ata"),  # irrelevant category
        _ipe_row("2025-01-10", "Fato Relevante", "antigo"),  # too old
        _ipe_row("2026-10-09", "Fato Relevante", "outra", cnpj="07.859.971/0001-30"),
    ]
    responses.get(_url(cvm.IPE_ZIP, 2026), body=_zip(cvm.IPE_CSV.format(year=2026), rows))
    responses.get(_url(cvm.IPE_ZIP, 2025), status=404)
    provider = CvmCompanyFilingsProvider(_client(tmp_path), today=lambda: TODAY)
    stock = Stock("PETR4", cnpj="33000167000101")

    filings = provider.list_filings(stock, days=90)
    assert [f.title for f in filings] == ["fato", "comunicado"]
    assert filings[0].source == "CVM (IPE)"
    assert provider.list_filings(Stock("PETR4")) == []
