"""CVM open data, FundosNET, asset resolution and filings routing."""

from __future__ import annotations

import csv
import io
import os
import zipfile
from datetime import date

import pytest
import responses

from crewai_invest_reporter.domain.errors import InvalidTickerError, ProviderError
from crewai_invest_reporter.domain.models import AssetClass, AssetRef
from crewai_invest_reporter.providers import cvm
from crewai_invest_reporter.providers.assets import OfficialFilingsRouter, RegistryAssetResolver
from crewai_invest_reporter.providers.cvm import (
    CvmCompanyFilingsProvider,
    CvmCompanyRegistry,
    CvmFiiRegistry,
    CvmOpenDataClient,
    RegistryEntry,
)
from crewai_invest_reporter.providers.fundosnet import BASE_URL as FNET
from crewai_invest_reporter.providers.fundosnet import FundosNetFilingsProvider
from crewai_invest_reporter.providers.http import build_session
from tests.fakes import FakeFilings

TODAY = date(2026, 10, 17)
PETRO = "33.000.167/0001-01"
MXRF = "97.521.225/0001-25"


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


def _mock_fii():
    rows = [
        {
            "CNPJ_Fundo_Classe": MXRF,
            "Nome_Fundo_Classe": "FII MAXI RENDA",
            "Codigo_ISIN": "BRMXRFCTF008",
        },
        {
            "CNPJ_Fundo_Classe": "11.222.333/0001-81",
            "Nome_Fundo_Classe": "Sem ISIN",
            "Codigo_ISIN": "",
        },
    ]
    responses.get(_url(cvm.FII_ZIP, 2025), status=404)
    responses.get(_url(cvm.FII_ZIP, 2026), body=_zip(cvm.FII_GENERAL_CSV.format(year=2026), rows))


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
def test_registries_map_tickers_and_isin_roots(tmp_path):
    _mock_fca()
    _mock_fii()
    client = _client(tmp_path)
    companies = CvmCompanyRegistry(client, today=lambda: TODAY)
    fiis = CvmFiiRegistry(client, today=lambda: TODAY)

    assert companies.lookup("petr4") == RegistryEntry("33000167000101", "PETROBRAS")
    assert companies.lookup("VALE3") is None
    assert fiis.lookup("MXRF11") == RegistryEntry("97521225000125", "FII MAXI RENDA")
    assert fiis.lookup_cnpj("97521225000125").name == "FII MAXI RENDA"
    assert "PETR4" in companies.tickers()
    assert fiis.tickers() == {"MXRF11": RegistryEntry("97521225000125", "FII MAXI RENDA")}


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
    asset = AssetRef("PETR4", AssetClass.STOCK, cnpj="33000167000101")

    filings = provider.list_filings(asset, days=90)
    assert [f.title for f in filings] == ["fato", "comunicado"]
    assert filings[0].source == "CVM (IPE)"
    assert provider.list_filings(AssetRef("X", AssetClass.STOCK)) == []


@responses.activate
def test_fundosnet_lists_recent_documents():
    responses.get(
        f"{FNET}/pesquisarGerenciadorDocumentosDados",
        json={
            "data": [
                {
                    "id": 1339341,
                    "dataEntrega": "02/10/2026 19:47",
                    "categoriaDocumento": "Relatórios",
                    "tipoDocumento": "Relatório Gerencial",
                    "especieDocumento": "",
                    "dataReferencia": "31/08/2026",
                },
                {"id": 1, "dataEntrega": "01/01/2025 10:00", "categoriaDocumento": "Antigo"},
                {"id": 2, "dataEntrega": "invalid"},
            ]
        },
        match=[
            responses.matchers.query_param_matcher(
                {"cnpjFundo": "97521225000125"}, strict_match=False
            )
        ],
    )
    asset = AssetRef("MXRF11", AssetClass.REAL_ESTATE_FUND, cnpj="97521225000125")
    filings = FundosNetFilingsProvider(today=lambda: TODAY).list_filings(asset, days=90)
    assert len(filings) == 1
    assert filings[0].title == "Relatório Gerencial"
    assert filings[0].url == f"{FNET}/downloadDocumento?id=1339341"
    assert filings[0].delivered_on == date(2026, 10, 2)


@responses.activate
def test_fundosnet_errors_become_provider_errors():
    responses.get(f"{FNET}/pesquisarGerenciadorDocumentosDados", status=500)
    asset = AssetRef("MXRF11", AssetClass.REAL_ESTATE_FUND, cnpj="97521225000125")
    with pytest.raises(ProviderError, match="HTTP 500"):
        FundosNetFilingsProvider(session=build_session(retries=0)).list_filings(asset)


class FakeRegistry:
    def __init__(self, by_ticker=None, by_cnpj=None, error=None):
        self.by_ticker, self.by_cnpj, self.error = by_ticker or {}, by_cnpj or {}, error

    def lookup(self, ticker):
        if self.error:
            raise self.error
        return self.by_ticker.get(ticker)

    def lookup_cnpj(self, cnpj):
        if self.error:
            raise self.error
        return self.by_cnpj.get(cnpj)


@pytest.fixture
def resolver():
    companies = FakeRegistry(
        {
            "PETR4": RegistryEntry("33000167000101", "PETROBRAS"),
            "TAEE11": RegistryEntry("07859971000130", "TAESA"),
        }
    )
    mxrf = RegistryEntry("97521225000125", "MAXI RENDA")
    fiis = FakeRegistry({"MXRF11": mxrf}, {"97521225000125": mxrf})
    return RegistryAssetResolver(companies, fiis)


@pytest.mark.parametrize(
    ("identifier", "asset_class", "cnpj"),
    [
        ("petr4", AssetClass.STOCK, "33000167000101"),
        ("TAEE11", AssetClass.STOCK, "07859971000130"),  # unit, not an FII
        ("MXRF11", AssetClass.REAL_ESTATE_FUND, "97521225000125"),
        ("BOVA11", AssetClass.ETF, None),
        ("AAPL34", AssetClass.BDR, None),
        ("VALE3", AssetClass.STOCK, None),
        ("97.521.225/0001-25", AssetClass.REAL_ESTATE_FUND, "97521225000125"),
        ("11.222.333/0001-81", AssetClass.MUTUAL_FUND, "11222333000181"),
    ],
)
def test_resolver(resolver, identifier, asset_class, cnpj):
    asset = resolver.resolve(identifier)
    assert (asset.asset_class, asset.cnpj) == (asset_class, cnpj)


def test_resolver_rejects_garbage(resolver):
    with pytest.raises(InvalidTickerError):
        resolver.resolve("rm -rf /")


def test_resolver_degrades_when_registries_fail():
    broken = FakeRegistry(error=ProviderError("CVM offline"))
    asset = RegistryAssetResolver(broken, broken).resolve("PETR4")
    assert (asset.identifier, asset.asset_class, asset.cnpj) == ("PETR4", AssetClass.STOCK, None)


def test_router_sends_assets_to_the_right_source():
    companies, funds, foreign = FakeFilings(), FakeFilings(), FakeFilings()
    router = OfficialFilingsRouter(
        company_filings=companies, fund_filings=funds, foreign_filings=foreign
    )
    router.list_filings(AssetRef("AAPL34", AssetClass.BDR, foreign_ticker="AAPL"))
    assert router.list_filings(AssetRef("BOVA11", AssetClass.ETF)) == []

    router.list_filings(AssetRef("PETR4", AssetClass.STOCK, cnpj="33000167000101"))
    router.list_filings(AssetRef("MXRF11", AssetClass.REAL_ESTATE_FUND, cnpj="97521225000125"))
    router.list_filings(AssetRef("11222333000181", AssetClass.MUTUAL_FUND, cnpj="11222333000181"))
    assert router.list_filings(AssetRef("XPTO34", AssetClass.BDR)) == []  # unknown US ticker
    assert router.list_filings(AssetRef("VALE3", AssetClass.STOCK)) == []  # no CNPJ known

    assert [c[0] for c in companies.calls] == ["PETR4"]
    assert [c[0] for c in funds.calls] == ["MXRF11", "11222333000181"]
    assert [c[0] for c in foreign.calls] == ["AAPL34"]


def test_resolver_maps_bdrs_to_their_us_ticker():
    resolver = RegistryAssetResolver(
        FakeRegistry(), FakeRegistry(), bdr_underlying={"M1TA34": "META"}.get
    )
    asset = resolver.resolve("M1TA34")
    assert (asset.asset_class, asset.foreign_ticker) == (AssetClass.BDR, "META")
