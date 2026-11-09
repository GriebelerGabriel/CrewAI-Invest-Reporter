from __future__ import annotations

import io

import pytest

from crewai_invest_reporter.domain.cnpj import cnpj_digits
from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import AssetClass, AssetRef
from crewai_invest_reporter.domain.tickers import normalize_ticker
from crewai_invest_reporter.providers.wallet import WalletFile
from crewai_invest_reporter.web import create_app, display_id, format_br, format_quantity
from tests.fakes import b3_position_file, xlsx

FUND_CNPJ = "11.222.333/0001-81"


def _resolve(identifier):
    cnpj = cnpj_digits(identifier)
    if cnpj:
        return AssetRef(cnpj, AssetClass.MUTUAL_FUND, None, cnpj)
    ticker = normalize_ticker(identifier)  # raises InvalidTickerError on garbage
    asset_class = AssetClass.REAL_ESTATE_FUND if ticker.endswith("11") else AssetClass.STOCK
    return AssetRef(ticker, asset_class, f"Empresa {ticker}", "33000167000101")


@pytest.fixture
def wallet(tmp_path):
    return WalletFile(tmp_path / "wallet.json")


@pytest.fixture
def client(wallet):
    app = create_app(wallet, _resolve, suggestions=lambda: [("PETR4", "PETROBRAS")])
    return app.test_client()


def _add_stock(client, ticker="petr4", amount="1.000"):
    return client.post("/add", data={"kind": "ticker", "identifier": ticker, "amount": amount})


def _add_fund(client, amount="5.000,50", name="Fundo XP"):
    data = {"kind": "fund", "identifier": FUND_CNPJ, "amount": amount, "name": name}
    return client.post("/add", data=data)


def test_page_lists_suggestions_and_starts_empty(client):
    html = client.get("/").get_data(as_text=True)
    assert '<option value="PETR4">PETROBRAS</option>' in html
    assert "Nenhum ativo ainda" in html


def test_add_stock_by_ticker_and_fund_by_cnpj(client, wallet):
    response = _add_stock(client)
    assert response.status_code == 302
    assert "salvo=PETR4" in response.location
    _add_fund(client)

    stock, fund = wallet.load()
    assert (stock.identifier, stock.amount, stock.name) == ("PETR4", 1000, "Empresa PETR4")
    assert (fund.identifier, fund.amount, fund.name, fund.by_ticker) == (
        "11222333000181",
        5000.5,
        "Fundo XP",
        False,
    )

    html = client.get("/?salvo=11222333000181").get_data(as_text=True)
    assert "11.222.333/0001-81 salvo na carteira" in html
    assert "1.000 <small>ações</small>" in html
    assert "<small>R$</small> 5.000,50" in html


def test_entries_are_listed_by_asset_type(client):
    _add_fund(client)
    _add_stock(client, "MXRF11", "250")
    _add_stock(client, "PETR4", "100")
    html = client.get("/").get_data(as_text=True)
    assert (
        html.index("PETR4</strong>")
        < html.index("MXRF11</strong>")
        < html.index("0001-81</strong>")
    )
    assert "250 <small>cotas</small>" in html


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"kind": "ticker", "identifier": "PETR4", "amount": "0"}, "uma quantidade maior que zero"),
        ({"kind": "fund", "identifier": FUND_CNPJ, "amount": "abc"}, "um valor maior que zero"),
        ({"kind": "ticker", "identifier": "../x", "amount": "10"}, "Não reconhecemos"),
        ({"kind": "fund", "identifier": "PETR4", "amount": "10"}, "Digite um CNPJ válido"),
        ({"kind": "ticker", "identifier": FUND_CNPJ, "amount": "10"}, "Isso é um CNPJ"),
    ],
)
def test_invalid_input_is_rejected_and_kept_in_the_form(client, wallet, data, message):
    response = client.post("/add", data=data)
    html = response.get_data(as_text=True)
    assert response.status_code == 400
    assert message in html
    assert f'value="{data["amount"]}"' in html
    assert wallet.load() == []


def test_edit_prefills_the_right_form(client):
    _add_stock(client, "PETR4", "1.000")
    _add_fund(client)

    html = client.get("/?editar=PETR4").get_data(as_text=True)
    assert "Editar PETR4" in html and 'value="1.000"' in html and '" readonly>' in html

    html = client.get("/?editar=11222333000181").get_data(as_text=True)
    assert "Editar fundo" in html and 'value="5.000,50"' in html and 'value="Fundo XP"' in html

    assert '" readonly>' not in client.get("/?editar=NOPE3").get_data(as_text=True)


def test_saving_again_replaces_the_amount(client, wallet):
    _add_stock(client, "PETR4", "100")
    _add_stock(client, "PETR4", "150")
    assert [(e.identifier, e.amount) for e in wallet.load()] == [("PETR4", 150)]


def test_remove(client, wallet):
    _add_stock(client)
    response = client.post("/remove", data={"identifier": "PETR4"})
    assert "removido=PETR4" in response.location
    assert wallet.load() == []
    assert "PETR4 removido da carteira" in client.get("/?removido=PETR4").get_data(as_text=True)


def test_cross_site_posts_are_refused(client, wallet):
    response = client.post(
        "/add",
        data={"kind": "ticker", "identifier": "PETR4", "amount": "10"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert wallet.load() == []
    same_site = {"Origin": "http://localhost"}
    assert client.post("/remove", data={"identifier": "X"}, headers=same_site).status_code == 302


def test_page_still_works_when_suggestions_fail(wallet):
    def broken():
        raise ProviderError("CVM offline")

    app = create_app(wallet, _resolve, suggestions=broken)
    assert app.test_client().get("/").status_code == 200


def test_formatting_helpers():
    assert format_br(1234.5) == "1.234,50"
    assert format_quantity(1000) == "1.000"
    assert format_quantity(2.5) == "2,50"
    assert display_id("11222333000181") == "11.222.333/0001-81"
    assert display_id("PETR4") == "PETR4"


def _upload(client, content, filename="posicao-2026-10-20.xlsx"):
    data = {"file": (io.BytesIO(content), filename)}
    return client.post("/import", data=data, content_type="multipart/form-data")


def test_import_replaces_tickers_and_keeps_funds(client, wallet):
    _add_stock(client, "VALE3", "10")  # sold since: not in the B3 file any more
    _add_fund(client)

    response = _upload(client, b3_position_file())
    assert response.status_code == 302
    assert "importados=2" in response.location

    assert sorted((e.identifier, e.amount) for e in wallet.load()) == [
        ("11222333000181", 5000.5),
        ("MXRF11", 1000.0),
        ("PETR4", 150.0),
    ]
    html = client.get("/?importados=2").get_data(as_text=True)
    assert "2 ativos importados da B3" in html


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not a spreadsheet", "Não consegui ler esse arquivo"),
        (xlsx({"Renda Fixa": [["Produto", "Quantidade"], ["CDB", 1]]}), "Nenhuma ação"),
    ],
)
def test_import_rejects_unusable_files(client, wallet, content, message):
    _add_stock(client, "PETR4", "10")
    response = _upload(client, content)
    assert response.status_code == 400
    assert message in response.get_data(as_text=True)
    assert [e.identifier for e in wallet.load()] == ["PETR4"]


def test_import_without_a_file(client):
    response = client.post("/import", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "Escolha o arquivo" in response.get_data(as_text=True)


def test_import_rejects_huge_uploads(client):
    response = _upload(client, b"x" * (6 * 1024 * 1024))
    assert response.status_code == 413
    assert "limite é 5 MB" in response.get_data(as_text=True)
