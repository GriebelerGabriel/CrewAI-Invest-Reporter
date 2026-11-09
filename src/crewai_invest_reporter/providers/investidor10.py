"""Fundamentals provider that scrapes public asset pages on investidor10.com.br.

The module is split in two responsibilities:

* ``Investidor10Parser`` turns page HTML into raw label -> text pairs (pure, easy to test);
* ``Investidor10Provider`` resolves URLs, fetches pages and maps raw text to typed metrics.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import requests
from bs4 import BeautifulSoup

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import Fundamentals
from crewai_invest_reporter.domain.tickers import is_bdr, is_unit_like, normalize_ticker
from crewai_invest_reporter.providers.http import BROWSER_HEADERS, build_session
from crewai_invest_reporter.providers.parsing import parse_br_number, parse_br_percent

BASE_URL = "https://investidor10.com.br"

_NUMBER = r"([\-\+]?[0-9\.]+,[0-9]{1,2}|[\-\+]?[0-9\.]+)"
_CURRENCY = r"([0-9\.]+,[0-9]{2})"

# label -> (regex, suffix appended to the captured value)
_TEXT_PATTERNS: dict[str, tuple[str, str]] = {
    "Preço": (rf"está cotad[oa]\s+a\s+R\$\s*{_CURRENCY}", ""),
    "Variação (12M)": (rf"variaç[aã]o\s+de\s*{_NUMBER}\s*%", "%"),
    "P/L": (rf"P\s*/\s*L\s+de\s*{_NUMBER}", ""),
    "P/VP": (rf"P\s*/\s*VP\s+de\s*{_NUMBER}", ""),
    "Dividend Yield": (rf"Dividend\s*Yield[^0-9%]*{_NUMBER}\s*%", "%"),
    "Dividendos (12m)": (
        rf"Nos\s+últimos\s+12\s+meses,\s+distribuiu\s+um\s+total\s+de\s*R\$\s*{_CURRENCY}",
        "",
    ),
}
# BDR pages describe the underlying listing, e.g. "negociada na Nasdaq sob o ticker AAPL".
_UNDERLYING_TICKER = re.compile(
    r"(?:Nasdaq|NYSE|Nyse|bolsa americana)[^.]{0,60}?tickers?\s+([A-Z][A-Z.\-]{0,6})\b"
)
_DAILY_LIQUIDITY = re.compile(
    rf"Liquidez\s*Di[áa]ria\s*R\$\s*{_CURRENCY}\s*([MK])", re.IGNORECASE | re.DOTALL
)


class Investidor10Parser:
    """Extract raw indicator strings from an Investidor10 asset page."""

    def parse(self, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "lxml")
        faq_texts = list(self._faq_answers(soup))
        page_text = soup.get_text("\n", strip=True)
        text = "\n".join([*faq_texts, page_text])

        raw: dict[str, str] = {}
        for label, (pattern, suffix) in _TEXT_PATTERNS.items():
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                raw[label] = match.group(1).strip() + suffix

        liquidity = _DAILY_LIQUIDITY.search(text)
        if liquidity:
            raw["Liquidez Diária"] = f"R$ {liquidity.group(1)} {liquidity.group(2)}"
        return raw

    @staticmethod
    def underlying_ticker(html: str) -> str | None:
        """US ticker of the company behind a BDR page, if the page states it."""
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
        match = _UNDERLYING_TICKER.search(text)
        return match.group(1).rstrip(".-") if match else None

    @staticmethod
    def _faq_answers(soup: BeautifulSoup) -> Iterable[str]:
        """FAQ structured data (JSON-LD) holds the most stable, sentence-shaped indicators."""
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.get_text(strip=True))
            except (json.JSONDecodeError, TypeError):
                continue
            objects = data if isinstance(data, list) else [data]
            for obj in objects:
                if not isinstance(obj, dict) or obj.get("@type") != "FAQPage":
                    continue
                for question in obj.get("mainEntity") or []:
                    answer = question.get("acceptedAnswer") if isinstance(question, dict) else None
                    text = answer.get("text") if isinstance(answer, dict) else None
                    if isinstance(text, str) and text.strip():
                        yield text


class Investidor10Provider:
    def __init__(
        self,
        session: requests.Session | None = None,
        parser: Investidor10Parser | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._session = session or build_session(BROWSER_HEADERS)
        self._parser = parser or Investidor10Parser()
        self._timeout = timeout
        self._pages: dict[str, tuple[str, str]] = {}

    @staticmethod
    def candidate_urls(ticker: str) -> list[str]:
        """Investidor10 groups assets by category; try the most likely categories first.

        A ``11`` suffix is ambiguous (FII, ETF or share unit such as TAEE11), so all three
        categories are tried in order.
        """
        if is_bdr(ticker):
            categories = ["bdrs"]
        elif is_unit_like(ticker):
            categories = ["fiis", "etfs", "acoes"]
        else:
            categories = ["acoes"]
        return [f"{BASE_URL}/{category}/{ticker.lower()}/" for category in categories]

    def fetch(self, ticker: str) -> Fundamentals:
        papel = normalize_ticker(ticker)
        url, html = self._fetch_page(papel)
        raw = self._parser.parse(html)
        return Fundamentals(ticker=papel, source_url=url, metrics=self._map_metrics(raw), raw=raw)

    def underlying_ticker(self, bdr_ticker: str) -> str | None:
        """For a BDR such as AAPL34 or M1TA34, the underlying US ticker (AAPL, META)."""
        try:
            _, html = self._fetch_page(normalize_ticker(bdr_ticker))
        except ProviderError:
            return None
        return self._parser.underlying_ticker(html)

    def _fetch_page(self, papel: str) -> tuple[str, str]:
        """First candidate page that has indicators; memoized because callers share pages."""
        if papel in self._pages:
            return self._pages[papel]
        attempts: list[str] = []
        for url in self.candidate_urls(papel):
            try:
                response = self._session.get(url, timeout=self._timeout)
            except requests.RequestException as exc:
                attempts.append(f"{url} -> {exc}")
                continue
            if response.status_code != 200:
                attempts.append(f"{url} -> HTTP {response.status_code}")
                continue
            if not self._parser.parse(response.text):
                attempts.append(f"{url} -> no indicators found")
                continue
            self._pages[papel] = (url, response.text)
            return self._pages[papel]
        raise ProviderError(f"investidor10 has no data for {papel}: {'; '.join(attempts)}")

    @staticmethod
    def _map_metrics(raw: dict[str, str]) -> dict[str, Any]:
        return {
            "currentPrice": parse_br_number(raw.get("Preço")),
            "priceChange12m": parse_br_percent(raw.get("Variação (12M)")),
            "trailingPE": parse_br_number(raw.get("P/L")),
            "priceToBook": parse_br_number(raw.get("P/VP")),
            "dividendYield": parse_br_percent(raw.get("Dividend Yield")),
            "dividendsLast12m": parse_br_number(raw.get("Dividendos (12m)")),
        }
