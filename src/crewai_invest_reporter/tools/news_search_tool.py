from __future__ import annotations

import re
from urllib.parse import quote_plus

import feedparser
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class NewsSearchToolInput(BaseModel):
    query: str = Field(..., description="Search query, e.g. PETR4 Petrobras")
    max_results: int = Field(10, description="Maximum number of news items to return")
    days: int = Field(30, description="Lookback window in days")
    language: str = Field("pt-BR", description="Language code for results")
    region: str = Field("BR", description="Region code for results")


class NewsSearchTool(BaseTool):
    name: str = "news_search"
    description: str = (
        "Search recent news from reputable sources using Google News RSS"
        "and return a structured list (title, source, published, url). "
        "Use it to gather news context about a stock ticker/company."
    )
    args_schema: type[BaseModel] = NewsSearchToolInput

    def _run(
        self,
        query: str,
        max_results: int = 10,
        days: int = 30,
        language: str = "pt-BR",
        region: str = "BR",
    ) -> str:
        q = quote_plus(f"{query} when:{days}d")
        url = (
            "https://news.google.com/rss/search?q="
            f"{q}&hl={language}&gl={region}&ceid={region}:{language}"
        )

        feed = feedparser.parse(url)
        items = []

        max_results = max_results if max_results and max_results > 0 else 10

        excluded_title_patterns = [
            r"\bquanto\s+ganharia\b",
            r"\bquanto\s+renderia\b",
            r"\bse\s+(?:voce|você)\s+tivesse\s+investido\b",
            r"\bse\s+tivesse\s+investido\b",
            r"\bsimulador\b",
            r"\bsimula(?:c|ç)\b",
        ]

        excluded_title_regex = re.compile("|".join(excluded_title_patterns), re.IGNORECASE)

        for entry in feed.entries:
            if len(items) >= max_results:
                break

            title = entry.get("title", "") or ""
            if excluded_title_regex.search(title):
                continue

            source = ""
            if isinstance(entry.get("source"), dict):
                source = entry["source"].get("title", "")

            items.append(
                {
                    "title": title,
                    "source": source,
                    "published": entry.get("published", ""),
                    "url": entry.get("link", ""),
                }
            )

        if not items:
            return f"No news found for query='{query}'. RSS url={url}"

        return str({"query": query, "rss_url": url, "items": items})
