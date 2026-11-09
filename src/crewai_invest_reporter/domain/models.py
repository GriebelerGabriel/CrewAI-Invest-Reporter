"""Domain models shared by providers, crews and services.

These are plain immutable dataclasses with no dependency on any external service, so every
layer can depend on them without pulling infrastructure concerns along.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any


class AssetClass(str, Enum):
    """The stocks and funds this app researches, independent of any provider's taxonomy."""

    STOCK = "STOCK"
    REAL_ESTATE_FUND = "REAL_ESTATE_FUND"
    ETF = "ETF"
    BDR = "BDR"
    MUTUAL_FUND = "MUTUAL_FUND"

    @property
    def is_listed(self) -> bool:
        """Whether assets of this class trade on B3 under a ticker we can research."""
        return self in _LISTED_CLASSES


_LISTED_CLASSES = frozenset(
    {AssetClass.STOCK, AssetClass.REAL_ESTATE_FUND, AssetClass.ETF, AssetClass.BDR}
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class AssetRef:
    """Identifies an asset to research: a B3 ticker or, for unlisted funds, a CNPJ."""

    identifier: str
    asset_class: AssetClass
    name: str | None = None
    cnpj: str | None = None
    # For BDRs: the ticker of the underlying company on its home exchange (e.g. AAPL).
    foreign_ticker: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.identifier} ({self.name})" if self.name else self.identifier

    @property
    def report_name(self) -> str:
        prefix = "FUNDO_" if self.identifier.isdigit() else ""
        return f"{prefix}{self.identifier}_investment_report"


@dataclass(frozen=True)
class NewsItem:
    title: str
    source: str
    published: str
    url: str


@dataclass(frozen=True)
class Filing:
    """An official document filed with a regulator (CVM, SEC) or B3's FundosNET."""

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
class Document:
    """Text extracted from a remote document (PDF, HTML, XML or plain text)."""

    url: str
    content_type: str
    text: str
    pages: int | None = None


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
    """Fundamental indicators for a single listed asset."""

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
    """Everything collected about one asset before any LLM is involved.

    Collection is deterministic (fixed sources, fixed selection rules and text budgets), so
    the agents only analyze this bundle and two runs on the same day see the same inputs.
    """

    asset: AssetRef
    news: tuple[NewsItem, ...] = ()
    fundamentals: Fundamentals | None = None
    documents: tuple[DocumentExcerpt, ...] = ()
    available_filings: tuple[Filing, ...] = ()
    gaps: tuple[str, ...] = ()
    collected_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": {key: value for key, value in asdict(self.asset).items() if value},
            "collected_at": self.collected_at.isoformat(),
            "news": [asdict(item) for item in self.news],
            "fundamentals": self.fundamentals.to_dict() if self.fundamentals else None,
            "documents": [excerpt.to_dict() for excerpt in self.documents],
            "other_recent_filings": [f.to_dict() for f in self.available_filings],
            "data_gaps": list(self.gaps),
        }


@dataclass(frozen=True)
class Holding:
    """A single position held by the investor at a financial institution."""

    name: str
    asset_class: AssetClass
    institution: str
    market_value: float
    ticker: str | None = None
    cnpj: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    invested_amount: float | None = None
    profit: float | None = None
    currency: str = "BRL"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["asset_class"] = self.asset_class.value
        return {key: value for key, value in data.items() if value is not None}


@dataclass(frozen=True)
class Portfolio:
    """A consolidated snapshot of holdings across one or more institutions."""

    holdings: tuple[Holding, ...]
    retrieved_at: datetime = field(default_factory=utc_now)
    errors: tuple[str, ...] = ()

    @property
    def total_value(self) -> float:
        return sum(h.market_value for h in self.holdings)

    def research_targets(self) -> list[AssetRef]:
        """One entry per asset (tickers held at two institutions merged), largest first."""
        values: dict[str, float] = defaultdict(float)
        refs: dict[str, AssetRef] = {}
        for holding in self.holdings:
            ref = _research_ref(holding)
            if ref is not None:
                values[ref.identifier] += holding.market_value
                refs.setdefault(ref.identifier, ref)
        return [refs[key] for key in sorted(values, key=values.__getitem__, reverse=True)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieved_at": self.retrieved_at.isoformat(),
            "total_value": round(self.total_value, 2),
            "holdings": [h.to_dict() for h in self.holdings],
            "errors": list(self.errors),
        }


def _research_ref(holding: Holding) -> AssetRef | None:
    if holding.ticker and holding.asset_class.is_listed:
        return AssetRef(holding.ticker, holding.asset_class, holding.name, holding.cnpj)
    if holding.cnpj:
        return AssetRef(holding.cnpj, holding.asset_class, holding.name, holding.cnpj)
    return None
