"""Fundamentals provider that scrapes public stock pages on investidor10.com.br.

The module is split in two responsibilities:

* ``Investidor10Parser`` turns page HTML into raw label -> text pairs (pure, easy to test);
* ``Investidor10Provider`` fetches pages and maps raw text to typed metrics.
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
from crewai_invest_reporter.domain.tickers import normalize_ticker
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

    @staticmethod
    def page_url(ticker: str) -> str:
        return f"{BASE_URL}/acoes/{ticker.lower()}/"

    def fetch(self, ticker: str) -> Fundamentals:
        papel = normalize_ticker(ticker)
        url = self.page_url(papel)
        try:
            response = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            raise ProviderError(f"investidor10 unreachable for {papel}: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(
                f"investidor10 has no data for {papel}: HTTP {response.status_code}"
            )
        raw = self._parser.parse(response.text)
        if not raw:
            raise ProviderError(f"investidor10 has no data for {papel}: no indicators found")
        return Fundamentals(ticker=papel, source_url=url, metrics=self._map_metrics(raw), raw=raw)

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
