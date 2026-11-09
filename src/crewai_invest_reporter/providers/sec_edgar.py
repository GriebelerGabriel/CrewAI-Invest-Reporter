"""Official US filings from SEC EDGAR (free, no API key), used for BDRs.

A BDR (e.g. AAPL34) represents shares of a US company (AAPL); that company's 8-K earnings
press releases, 10-Q and 10-K reports are the primary sources for its fundamentals.
SEC asks clients to identify themselves via the User-Agent header (``SEC_USER_AGENT``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta
from functools import cached_property
from typing import Any

import requests

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import AssetRef, Filing
from crewai_invest_reporter.providers.cache import DiskCachedFetcher
from crewai_invest_reporter.providers.http import build_session

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}"

EARNINGS_RELEASE = "Earnings Release (8-K)"
FORM_CATEGORIES = {
    "10-K": "Annual Report (10-K)",
    "10-Q": "Quarterly Report (10-Q)",
    "8-K": "Current Report (8-K)",
}
EARNINGS_ITEM = "2.02"  # 8-K item "Results of Operations and Financial Condition"
MAX_EXHIBIT_LOOKUPS = 2  # bound the extra index.json calls per asset


class SecEdgarFilingsProvider:
    SOURCE = "SEC EDGAR"

    def __init__(
        self,
        fetcher: DiskCachedFetcher,
        session: requests.Session | None = None,
        user_agent: str = "crewai-invest-reporter/0.1",
        timeout: float = 30.0,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._fetcher = fetcher
        self._session = session or build_session({"User-Agent": user_agent})
        self._timeout = timeout
        self._today = today

    def list_filings(self, asset: AssetRef, *, days: int = 180, limit: int = 20) -> list[Filing]:
        if not asset.foreign_ticker:
            return []
        cik = self._cik_by_ticker.get(asset.foreign_ticker.upper())
        if cik is None:
            raise ProviderError(f"SEC EDGAR has no company with ticker {asset.foreign_ticker}")

        recent = (
            self._get_json(SUBMISSIONS_URL.format(cik=cik)).get("filings", {}).get("recent", {})
        )
        since = self._today() - timedelta(days=days)
        filings: list[Filing] = []
        exhibit_lookups = 0
        for row in _rows(recent):
            form = row.get("form", "")
            if form not in FORM_CATEGORIES:
                continue
            try:
                filed = date.fromisoformat(row.get("filingDate", ""))
            except ValueError:
                continue
            if filed < since:
                break  # EDGAR lists filings newest first
            accession = row.get("accessionNumber", "").replace("-", "")
            folder = ARCHIVES_URL.format(cik=cik, accession=accession)
            category, url = FORM_CATEGORIES[form], f"{folder}/{row.get('primaryDocument', '')}"
            items = row.get("items") or ""
            if form == "8-K" and EARNINGS_ITEM in items and exhibit_lookups < MAX_EXHIBIT_LOOKUPS:
                exhibit_lookups += 1
                category = EARNINGS_RELEASE
                url = self._press_release_url(folder) or url
            description = (row.get("primaryDocDescription") or "").strip()
            title = form if description in ("", form) else f"{form} - {description}"
            if items:
                title += f" (items {items})"
            filings.append(
                Filing(
                    title=title,
                    category=category,
                    delivered_on=filed,
                    url=url,
                    source=self.SOURCE,
                    reference=row.get("reportDate") or None,
                )
            )
            if len(filings) >= limit:
                break
        return filings

    def _press_release_url(self, folder: str) -> str | None:
        """The earnings press release is the 8-K's exhibit 99 (e.g. ``a8-kex991q3.htm``)."""
        try:
            items = self._get_json(f"{folder}/index.json")["directory"]["item"]
        except (ProviderError, KeyError, TypeError):
            return None
        for item in items:
            name = item.get("name", "")
            if "ex99" in name.lower().replace("-", "").replace("_", "") and name.endswith(".htm"):
                return f"{folder}/{name}"
        return None

    @cached_property
    def _cik_by_ticker(self) -> dict[str, int]:
        raw = self._fetcher.get(TICKERS_URL, cache_key="company_tickers.json")
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("SEC ticker list is not valid JSON") from exc
        return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            response = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"SEC EDGAR request failed: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(f"SEC EDGAR {url} returned HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("SEC EDGAR returned invalid JSON") from exc


def _rows(columns: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """EDGAR returns column arrays ({"form": [...], "filingDate": [...]}); zip them into rows."""
    if not columns:
        return []
    keys = list(columns)
    return [
        dict(zip(keys, values, strict=False)) for values in zip(*columns.values(), strict=False)
    ]
