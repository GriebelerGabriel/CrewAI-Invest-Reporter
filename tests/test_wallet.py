from __future__ import annotations

import pytest

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import AssetClass
from crewai_invest_reporter.providers.wallet import (
    ManualPortfolioProvider,
    WalletEntry,
    WalletFile,
)

PETR4 = WalletEntry("PETR4", AssetClass.STOCK, 100, "PETROBRAS", "33000167000101")
FUND = WalletEntry("11222333000181", AssetClass.MUTUAL_FUND, 5000.0, "Fundo XP", "11222333000181")


def test_missing_file_is_an_empty_wallet(tmp_path):
    assert WalletFile(tmp_path / "wallet.json").load() == []


def test_upsert_adds_then_replaces_in_place_and_remove_deletes(tmp_path):
    wallet = WalletFile(tmp_path / "data" / "wallet.json")
    wallet.upsert(PETR4)
    wallet.upsert(FUND)
    wallet.upsert(WalletEntry("PETR4", AssetClass.STOCK, 150, "PETROBRAS", "33000167000101"))
    assert [(e.identifier, e.amount) for e in wallet.load()] == [
        ("PETR4", 150),
        ("11222333000181", 5000.0),
    ]

    wallet.remove("PETR4")
    assert wallet.load() == [FUND]
    assert not list((tmp_path / "data").glob("*.tmp"))


def test_invalid_file_raises_provider_error(tmp_path):
    path = tmp_path / "wallet.json"
    path.write_text('{"assets": [{"identifier": "PETR4"}]}', encoding="utf-8")
    with pytest.raises(ProviderError, match="not valid"):
        WalletFile(path).load()


def test_provider_values_shares_at_the_quote_and_funds_as_typed(tmp_path):
    wallet = WalletFile(tmp_path / "wallet.json")
    wallet.save([PETR4, FUND])
    portfolio = ManualPortfolioProvider(wallet, quote={"PETR4": 38.5}.get).fetch_portfolio()

    stock, fund = portfolio.holdings
    assert (stock.ticker, stock.quantity, stock.unit_price, stock.market_value) == (
        "PETR4",
        100,
        38.5,
        3850.0,
    )
    assert (fund.ticker, fund.cnpj, fund.market_value) == (None, "11222333000181", 5000.0)
    assert [t.identifier for t in portfolio.research_targets()] == ["11222333000181", "PETR4"]
    assert portfolio.errors == ()


def test_provider_keeps_assets_whose_quote_fails(tmp_path):
    wallet = WalletFile(tmp_path / "wallet.json")
    wallet.save([PETR4, WalletEntry("VALE3", AssetClass.STOCK, 10)])

    def quote(ticker):
        if ticker == "PETR4":
            raise ProviderError("investidor10 offline")
        return None

    portfolio = ManualPortfolioProvider(wallet, quote).fetch_portfolio()
    assert [h.market_value for h in portfolio.holdings] == [0.0, 0.0]
    assert portfolio.errors == (
        "PETR4: no quote (investidor10 offline)",
        "VALE3: no quote available",
    )


def test_replace_tickers_keeps_funds_added_by_cnpj(tmp_path):
    wallet = WalletFile(tmp_path / "wallet.json")
    wallet.save([PETR4, FUND])
    mxrf = WalletEntry("MXRF11", AssetClass.REAL_ESTATE_FUND, 1000)
    wallet.replace_tickers([mxrf])
    assert wallet.load() == [mxrf, FUND]
