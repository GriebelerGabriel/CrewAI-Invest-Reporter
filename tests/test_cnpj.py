import pytest

from crewai_invest_reporter.domain.cnpj import cnpj_digits


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("97.521.225/0001-25", "97521225000125"),
        ("33000167000101", "33000167000101"),
        ("97.521.225/0001-26", None),  # wrong check digit
        ("11.111.111/1111-11", None),  # repeated digits
        ("PETR4", None),
        (None, None),
    ],
)
def test_cnpj_digits(raw, expected):
    assert cnpj_digits(raw) == expected
