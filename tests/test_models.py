import json

from crewai_invest_reporter.domain.models import AssetClass


def test_total_value(sample_portfolio):
    assert sample_portfolio.total_value == 5300.0


def test_to_dict_is_json_serializable_and_drops_empty_fields(sample_portfolio):
    data = sample_portfolio.to_dict()
    json.dumps(data)
    assert data["total_value"] == 5300.0
    assert data["holdings"][0]["asset_class"] == "STOCK"
    assert "quantity" not in data["holdings"][0]


def test_research_targets_merge_tickers_and_sort_by_size(sample_portfolio):
    targets = sample_portfolio.research_targets()
    # PETR4 held twice (3000 + 500) ranks above MXRF11 (1000) and the fund (800).
    assert [t.identifier for t in targets] == ["PETR4", "MXRF11", "11222333000181"]
    assert targets[2].asset_class is AssetClass.MUTUAL_FUND
    assert targets[2].report_name == "FUNDO_11222333000181_investment_report"
    assert targets[0].report_name == "PETR4_investment_report"


def test_asset_class_is_listed():
    assert AssetClass.STOCK.is_listed
    assert not AssetClass.MUTUAL_FUND.is_listed
