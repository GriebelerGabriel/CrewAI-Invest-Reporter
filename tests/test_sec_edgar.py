from __future__ import annotations

import json
from datetime import date

import pytest
import responses

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import AssetClass, AssetRef
from crewai_invest_reporter.providers.cache import DiskCachedFetcher
from crewai_invest_reporter.providers.http import build_session
from crewai_invest_reporter.providers.sec_edgar import (
    EARNINGS_RELEASE,
    TICKERS_URL,
    SecEdgarFilingsProvider,
)

TODAY = date(2026, 10, 17)
AAPL = AssetRef("AAPL34", AssetClass.BDR, foreign_ticker="AAPL")
FOLDER = "https://www.sec.gov/Archives/edgar/data/320193"


def _submissions():
    rows = [
        ("10-Q", "2026-07-31", "0000320193-26-000020", "aapl-20260627.htm", "", "10-Q"),
        ("8-K", "2026-07-30", "0000320193-26-000018", "aapl-20260730.htm", "2.02,9.01", "8-K"),
        ("4", "2026-07-15", "0000320193-26-000017", "form4.xml", "", "FORM 4"),
        ("8-K", "2026-04-20", "0001140361-26-015711", "ef2007_8k.htm", "5.02", "8-K"),
        ("10-K", "2025-10-30", "0000320193-25-000079", "aapl-20250927.htm", "", "10-K"),
    ]
    keys = [
        "form",
        "filingDate",
        "accessionNumber",
        "primaryDocument",
        "items",
        "primaryDocDescription",
    ]
    return {"filings": {"recent": {k: [r[i] for r in rows] for i, k in enumerate(keys)}}}


def _provider(tmp_path) -> SecEdgarFilingsProvider:
    session = build_session(retries=0)
    return SecEdgarFilingsProvider(
        DiskCachedFetcher(tmp_path, session=session), session=session, today=lambda: TODAY
    )


@responses.activate
def test_lists_recent_filings_and_resolves_the_earnings_press_release(tmp_path):
    responses.get(TICKERS_URL, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}})
    responses.get("https://data.sec.gov/submissions/CIK0000320193.json", json=_submissions())
    responses.get(
        f"{FOLDER}/000032019326000018/index.json",
        json={
            "directory": {
                "item": [{"name": "aapl-20260730.htm"}, {"name": "a8-kex991q3202606272026.htm"}]
            }
        },
    )
    filings = _provider(tmp_path).list_filings(AAPL, days=180)

    assert [f.category for f in filings] == [
        "Quarterly Report (10-Q)",
        EARNINGS_RELEASE,
        "Current Report (8-K)",
    ]  # Form 4 skipped, 10-K older than 180 days
    release = filings[1]
    assert release.url == f"{FOLDER}/000032019326000018/a8-kex991q3202606272026.htm"
    assert release.title == "8-K (items 2.02,9.01)"
    assert filings[0].url == f"{FOLDER}/000032019326000020/aapl-20260627.htm"


@responses.activate
def test_unknown_ticker_raises_and_missing_ticker_returns_nothing(tmp_path):
    responses.get(TICKERS_URL, body=json.dumps({}))
    provider = _provider(tmp_path)
    with pytest.raises(ProviderError, match="no company with ticker"):
        provider.list_filings(AAPL)
    assert provider.list_filings(AssetRef("XPTO34", AssetClass.BDR)) == []
