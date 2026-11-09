import pytest

from crewai_invest_reporter.providers.parsing import parse_br_number, parse_br_percent


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.234,56", 1234.56),
        ("R$ 10,50", 10.5),
        ("12,3%", 12.3),
        ("5.4", 5.4),
        ("1.000", 1000.0),
        ("-2,5", -2.5),
        ("+3,10", 3.1),
        ("R$\xa01.000.000,00", 1_000_000.0),
    ],
)
def test_parse_br_number(text, expected):
    assert parse_br_number(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", [None, "", "-", "n/a", "R$"])
def test_parse_br_number_invalid(text):
    assert parse_br_number(text) is None


def test_parse_br_percent():
    assert parse_br_percent("12,5%") == pytest.approx(0.125)
    assert parse_br_percent(None) is None
