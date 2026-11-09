from __future__ import annotations

import pytest

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.providers.b3_position import read_b3_positions
from tests.fakes import B3_STOCK_HEADER, b3_position_file, b3_stock_row, xlsx


def test_reads_listed_assets_summed_across_brokers():
    # PETR4 at two brokers is summed; "1.000" (text) is parsed; Tesouro and Total are skipped.
    assert read_b3_positions(b3_position_file()) == {"PETR4": 150.0, "MXRF11": 1000.0}


def test_header_may_come_after_title_rows():
    content = xlsx(
        {"ETF": [["Posição em 20/10/2026"], [], B3_STOCK_HEADER, b3_stock_row("BOVA11", "XP", 30)]}
    )
    assert read_b3_positions(content) == {"BOVA11": 30.0}


def test_file_without_listed_assets_is_empty():
    content = xlsx({"Renda Fixa": [["Produto", "Quantidade"], ["CDB", 1]]})
    assert read_b3_positions(content) == {}


@pytest.mark.parametrize("content", [b"not a spreadsheet", b"PK\x03\x04broken zip"])
def test_unreadable_file_raises_provider_error(content):
    with pytest.raises(ProviderError, match=r"Not a B3 \.xlsx"):
        read_b3_positions(content)
