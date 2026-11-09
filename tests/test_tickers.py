import pytest

from crewai_invest_reporter.domain.errors import InvalidTickerError
from crewai_invest_reporter.domain.tickers import (
    is_bdr,
    is_unit_like,
    normalize_ticker,
    try_normalize_ticker,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PETR4", "PETR4"),
        (" petr4.sa ", "PETR4"),
        ("MXRF11", "MXRF11"),
        ("TAEE11F", "TAEE11"),
        ("AAPL34", "AAPL34"),
    ],
)
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "PETR", "../etc", "PETR4; rm", "TOOLONG123"])
def test_normalize_ticker_rejects_invalid(raw):
    with pytest.raises(InvalidTickerError):
        normalize_ticker(raw)


def test_try_normalize_ticker_returns_none_for_non_tickers():
    assert try_normalize_ticker("12.345.678/0001-90") is None
    assert try_normalize_ticker(None) is None


def test_classifiers():
    assert is_bdr("AAPL34")
    assert not is_bdr("PETR4")
    assert is_unit_like("TAEE11")
