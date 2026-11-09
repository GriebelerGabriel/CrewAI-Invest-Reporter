from __future__ import annotations

import pytest
import responses
from responses import matchers

from crewai_invest_reporter.domain.errors import ConfigurationError, ProviderError
from crewai_invest_reporter.domain.models import AssetClass
from crewai_invest_reporter.providers.pluggy import (
    PluggyClient,
    PluggyInvestmentMapper,
    PluggyPortfolioProvider,
)
from crewai_invest_reporter.settings import PluggySettings

BASE = "https://api.pluggy.test"
ITEM = "11111111-1111-1111-1111-111111111111"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _client(clock: FakeClock | None = None) -> PluggyClient:
    return PluggyClient("cid", "secret", base_url=BASE, clock=clock or FakeClock())


def _mock_auth(api_key: str = "key-1") -> responses.BaseResponse:
    return responses.post(
        f"{BASE}/auth",
        json={"apiKey": api_key},
        match=[matchers.json_params_matcher({"clientId": "cid", "clientSecret": "secret"})],
    )


@responses.activate
def test_client_authenticates_once_and_sends_api_key_header():
    auth = _mock_auth()
    item = responses.get(
        f"{BASE}/items/{ITEM}",
        json={"id": ITEM, "connector": {"name": "Nubank"}},
        match=[matchers.header_matcher({"X-API-KEY": "key-1"})],
    )
    client = _client()
    client.get_item(ITEM)
    client.get_item(ITEM)
    assert auth.call_count == 1
    assert item.call_count == 2


@responses.activate
def test_client_refreshes_expired_api_key():
    clock = FakeClock()
    auth = _mock_auth()
    responses.get(f"{BASE}/items/{ITEM}", json={})
    client = _client(clock)
    client.get_item(ITEM)
    clock.now += 3 * 60 * 60
    client.get_item(ITEM)
    assert auth.call_count == 2


@responses.activate
def test_client_retries_once_on_401():
    auth = _mock_auth()
    responses.get(f"{BASE}/items/{ITEM}", status=401, json={"message": "expired"})
    responses.get(f"{BASE}/items/{ITEM}", json={"id": ITEM})
    assert _client().get_item(ITEM) == {"id": ITEM}
    assert auth.call_count == 2


@responses.activate
def test_client_paginates_investments():
    _mock_auth()
    for page in (1, 2):
        responses.get(
            f"{BASE}/investments",
            json={"page": page, "totalPages": 2, "results": [{"id": f"inv-{page}"}]},
            match=[
                matchers.query_param_matcher({"itemId": ITEM, "page": str(page), "pageSize": "500"})
            ],
        )
    assert [inv["id"] for inv in _client().list_investments(ITEM)] == ["inv-1", "inv-2"]


@responses.activate
def test_client_raises_provider_error_on_http_failure():
    responses.post(f"{BASE}/auth", status=403, json={"message": "invalid credentials"})
    with pytest.raises(ProviderError, match="HTTP 403"):
        _client().get_item(ITEM)


class TestMapper:
    mapper = PluggyInvestmentMapper()

    def test_maps_stock_with_fractional_ticker(self):
        holding = self.mapper.to_holding(
            {
                "id": "1",
                "type": "EQUITY",
                "subtype": "STOCK",
                "code": "PETR4F",
                "name": "PETROBRAS PN",
                "balance": 1234.5,
                "quantity": 30,
                "value": 41.15,
                "amountOriginal": 1000,
                "amountProfit": 234.5,
                "status": "ACTIVE",
            },
            fallback_institution="MeuPluggy",
        )
        assert holding is not None
        assert holding.asset_class is AssetClass.STOCK
        assert holding.ticker == "PETR4"
        assert holding.market_value == 1234.5
        assert holding.institution == "MeuPluggy"

    def test_maps_fund_with_its_cnpj(self):
        holding = self.mapper.to_holding(
            {
                "type": "MUTUAL_FUND",
                "subtype": "MULTIMARKET_FUND",
                "code": "11.222.333/0001-81",
                "name": "Fundo Multimercado",
                "balance": 500,
                "institution": {"name": "Nu Pagamentos"},
            },
            fallback_institution="MeuPluggy",
        )
        assert holding.asset_class is AssetClass.MUTUAL_FUND
        assert holding.ticker is None
        assert holding.cnpj == "11222333000181"
        assert holding.institution == "Nu Pagamentos"

    @pytest.mark.parametrize(
        ("type_", "subtype", "expected"),
        [
            ("EQUITY", "REAL_ESTATE_FUND", AssetClass.REAL_ESTATE_FUND),
            ("EQUITY", "BDR", AssetClass.BDR),
            ("EQUITY", None, AssetClass.STOCK),
            ("ETF", "ETF", AssetClass.ETF),
            ("MUTUAL_FUND", "MULTIMARKET_FUND", AssetClass.MUTUAL_FUND),
        ],
    )
    def test_asset_class_mapping(self, type_, subtype, expected):
        assert self.mapper.asset_class(type_, subtype) is expected

    @pytest.mark.parametrize(
        "investment",
        [
            {"type": "EQUITY", "balance": 10, "status": "TOTAL_WITHDRAWAL"},
            {"type": "EQUITY", "balance": 0},
            {"type": "EQUITY"},
            {"type": "EQUITY", "subtype": "OPTION", "code": "PETRA40", "balance": 10},
            {"type": "FIXED_INCOME", "subtype": "CDB", "name": "CDB Nubank", "balance": 500},
            {"type": "SECURITY", "subtype": "PGBL", "balance": 500},
            {"type": "COE", "balance": 500},
            {"type": "UNKNOWN", "balance": 500},
        ],
    )
    def test_skips_empty_withdrawn_and_non_researched_investments(self, investment):
        assert self.mapper.to_holding(investment, "Nubank") is None


class FakePluggyClient:
    def __init__(self, failing: set[str] | None = None) -> None:
        self.failing = failing or set()

    def get_item(self, item_id):
        if item_id in self.failing:
            raise ProviderError("boom")
        return {"connector": {"name": f"Bank-{item_id}"}}

    def list_investments(self, item_id):
        return [{"type": "EQUITY", "subtype": "STOCK", "code": "ITSA4", "balance": 100}]


def test_provider_consolidates_items_and_keeps_partial_failures():
    provider = PluggyPortfolioProvider(FakePluggyClient(failing={"b"}), ["a", "b"])
    portfolio = provider.fetch_portfolio()
    assert portfolio.total_value == 100
    assert {h.institution for h in portfolio.holdings} == {"Bank-a"}
    assert len(portfolio.errors) == 1 and "item b" in portfolio.errors[0]


def test_provider_raises_when_every_item_fails():
    provider = PluggyPortfolioProvider(FakePluggyClient(failing={"a"}), ["a"])
    with pytest.raises(ProviderError, match="Could not load"):
        provider.fetch_portfolio()


def test_from_settings_requires_credentials():
    with pytest.raises(ConfigurationError, match="PLUGGY_CLIENT_ID"):
        PluggyPortfolioProvider.from_settings(PluggySettings())
