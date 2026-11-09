"""News provider backed by the public Google News RSS search feed."""

from __future__ import annotations

import re
from urllib.parse import urlencode

import feedparser
import requests

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import NewsItem
from crewai_invest_reporter.providers.http import BROWSER_HEADERS, build_session

RSS_SEARCH_URL = "https://news.google.com/rss/search"

# Low-signal headlines (return simulators, "what if you had invested" pieces...).
DEFAULT_EXCLUDED_TITLE_PATTERNS = (
    r"\bquanto\s+ganharia\b",
    r"\bquanto\s+renderia\b",
    r"\bse\s+(?:voce|você)\s+tivesse\s+investido\b",
    r"\bse\s+tivesse\s+investido\b",
    r"\bsimulador\b",
    r"\bsimula(?:c|ç)",
)


class GoogleNewsProvider:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 15.0,
        excluded_title_patterns: tuple[str, ...] = DEFAULT_EXCLUDED_TITLE_PATTERNS,
    ) -> None:
        self._session = session or build_session(BROWSER_HEADERS)
        self._timeout = timeout
        self._excluded = re.compile("|".join(excluded_title_patterns), re.IGNORECASE)

    @staticmethod
    def build_url(query: str, days: int, language: str, region: str) -> str:
        params = {
            "q": f"{query} when:{days}d",
            "hl": language,
            "gl": region,
            "ceid": f"{region}:{language}",
        }
        return f"{RSS_SEARCH_URL}?{urlencode(params)}"

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
        days: int = 30,
        language: str = "pt-BR",
        region: str = "BR",
    ) -> list[NewsItem]:
        url = self.build_url(query, days, language, region)
        try:
            # Fetch with requests (feedparser has no timeout control), then parse the body.
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderError(f"Google News request failed: {exc}") from exc

        feed = feedparser.parse(response.content)
        items: list[NewsItem] = []
        for entry in feed.entries:
            if len(items) >= max_results:
                break
            item = self._to_news_item(entry)
            if item is not None:
                items.append(item)
        return items

    def _to_news_item(self, entry: dict) -> NewsItem | None:
        title = entry.get("title") or ""
        if not title or self._excluded.search(title):
            return None
        source = entry.get("source")
        return NewsItem(
            title=title,
            source=source.get("title", "") if isinstance(source, dict) else "",
            published=entry.get("published", ""),
            url=entry.get("link", ""),
        )
