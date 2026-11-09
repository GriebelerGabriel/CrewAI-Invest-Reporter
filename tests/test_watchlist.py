import pytest

from crewai_invest_reporter.domain.errors import ConfigurationError
from crewai_invest_reporter.watchlist import read_watchlist


def test_reads_tickers_skipping_comments_blanks_and_duplicates(tmp_path):
    path = tmp_path / "watchlist.txt"
    path.write_text("# minhas ações\npetr4\n\nVALE3  # mineração\nPETR4\nBRBI11\n", "utf-8")
    assert read_watchlist(path) == ["PETR4", "VALE3", "BRBI11"]


@pytest.mark.parametrize(
    ("content", "message"),
    [(None, "not found"), ("# vazio\n", "has no tickers"), ("PETR4\nPETROBRAS\n", ":2:")],
)
def test_invalid_watchlists_fail_clearly(tmp_path, content, message):
    path = tmp_path / "watchlist.txt"
    if content is not None:
        path.write_text(content, "utf-8")
    with pytest.raises(ConfigurationError, match=message):
        read_watchlist(path)


def test_the_repository_watchlist_is_valid():
    from pathlib import Path

    tickers = read_watchlist(Path(__file__).parent.parent / "watchlist.txt")
    assert len(tickers) == 9
