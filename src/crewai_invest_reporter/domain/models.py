"""Domain models shared by providers, crews and services.

These are plain immutable dataclasses with no dependency on any external service, so every
layer can depend on them without pulling infrastructure concerns along.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Stock:
    """A company listed on B3: its ticker, plus the legal name and CNPJ from CVM's registry."""

    ticker: str
    name: str | None = None
    # Without a CNPJ the company's CVM filings cannot be looked up.
    cnpj: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.ticker} ({self.name})" if self.name else self.ticker


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    published: str
    url: str


@dataclass(frozen=True)
class Filing:
    """An official document a company filed with CVM."""

    title: str
    category: str
    delivered_on: date
    url: str
    source: str
    reference: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["delivered_on"] = self.delivered_on.isoformat()
        return {key: value for key, value in data.items() if value}


@dataclass(frozen=True)
class DocumentExcerpt:
    """The part of an official document handed to the agents, with its provenance."""

    filing: Filing
    text: str = ""
    truncated: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = self.filing.to_dict()
        if self.error:
            data["error"] = self.error
        else:
            data["text"] = self.text
            data["truncated"] = self.truncated
        return data


@dataclass(frozen=True)
class Fundamentals:
    """Fundamental indicators for a single stock."""

    ticker: str
    source_url: str
    metrics: dict[str, float | None]
    raw: dict[str, str]
    retrieved_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["retrieved_at"] = self.retrieved_at.isoformat()
        return data


@dataclass(frozen=True)
class ResearchDossier:
    """Everything collected about one stock before any LLM is involved.

    Collection is deterministic (fixed sources, fixed selection rules and text budgets), so
    the agents only analyze this bundle and two runs on the same day see the same inputs.
    """

    stock: Stock
    news: tuple[NewsItem, ...] = ()
    fundamentals: Fundamentals | None = None
    documents: tuple[DocumentExcerpt, ...] = ()
    available_filings: tuple[Filing, ...] = ()
    gaps: tuple[str, ...] = ()
    collected_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stock": {key: value for key, value in asdict(self.stock).items() if value},
            "collected_at": self.collected_at.isoformat(),
            "news": [asdict(item) for item in self.news],
            "fundamentals": self.fundamentals.to_dict() if self.fundamentals else None,
            "documents": [excerpt.to_dict() for excerpt in self.documents],
            "other_recent_filings": [f.to_dict() for f in self.available_filings],
            "data_gaps": list(self.gaps),
        }


BRAZIL = ZoneInfo("America/Sao_Paulo")


def today_in_brazil() -> date:
    """The B3 trading calendar runs on Brasília time, whatever the server's timezone."""
    return datetime.now(BRAZIL).date()


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    kind: str = "news"  # "news" or "filing"
    cited: bool = False  # used in the briefing (shown in the email) or only seen


@dataclass(frozen=True)
class Digest:
    """One stock's part of a day's email: generated once per day, sent to every subscriber."""

    day: date
    ticker: str
    name: str | None = None
    price: float | None = None
    # Versus the previous digest's price, as a fraction (-0.012 = -1.2%).
    price_change: float | None = None
    # None when there was nothing new since the previous digest.
    summary: str | None = None
    sources: tuple[Source, ...] = ()


@dataclass(frozen=True)
class Subscriber:
    id: int
    email: str
    tickers: tuple[str, ...]
    unsubscribe_token: str
    confirmation_sent_at: datetime | None = None


@dataclass(frozen=True)
class Verdict:
    """The structured conclusion of a full report, as the rating agent stated it."""

    rating: str  # Excellent to invest / Good to invest / Neutral / Watch / Bad to invest
    probability_good: int  # % chance of being a good investment
    probability_loss: int  # % chance of losing money
    confidence: str  # alta / média / baixa


@dataclass(frozen=True)
class Analysis:
    """What the report crew produces: the markdown report, and its verdict if one was parsed."""

    report: str
    verdict: Verdict | None = None


@dataclass(frozen=True)
class Rating:
    """A verdict kept with the market data of its day, to compare later with what happened."""

    day: date
    ticker: str
    verdict: Verdict
    price: float | None
    metrics: dict[str, float | None]
    model: str
    report: str
