import pytest

from crewai_invest_reporter.domain.errors import InvalidTickerError
from crewai_invest_reporter.domain.tickers import normalize_ticker


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PETR4", "PETR4"),
        (" petr4.sa ", "PETR4"),
        ("BRBI11", "BRBI11"),
        ("TAEE11F", "TAEE11"),
    ],
)
def test_normalize_ticker(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "PETR", "../etc", "PETR4; rm", "TOOLONG123"])
def test_normalize_ticker_rejects_invalid(raw):
    with pytest.raises(InvalidTickerError):
        normalize_ticker(raw)
