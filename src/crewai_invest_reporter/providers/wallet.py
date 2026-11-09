"""Holdings typed in by hand on the wallet page, kept in a small JSON file.

* ``WalletFile`` loads and saves the entries (``WALLET_FILE``, default ``wallet.json``).
* ``ManualPortfolioProvider`` turns them into a ``Portfolio``, the same as Open Finance does.

An entry added by ticker holds a number of shares and is valued at today's quote; an entry
added by CNPJ (funds, which have no quote) holds its value in R$.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crewai_invest_reporter.domain.errors import InvestReporterError, ProviderError
from crewai_invest_reporter.domain.models import AssetClass, Holding, Portfolio


@dataclass(frozen=True)
class WalletEntry:
    identifier: str  # B3 ticker, or the fund's CNPJ (digits only)
    asset_class: AssetClass
    amount: float  # by ticker: number of shares; by CNPJ: value in R$
    name: str | None = None
    cnpj: str | None = None

    @property
    def by_ticker(self) -> bool:
        return not self.identifier.isdigit()

    def to_dict(self) -> dict[str, Any]:
        data = {
            "identifier": self.identifier,
            "asset_class": self.asset_class.value,
            "amount": self.amount,
            "name": self.name,
            "cnpj": self.cnpj,
        }
        return {key: value for key, value in data.items() if value is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WalletEntry:
        return cls(
            identifier=str(data["identifier"]),
            asset_class=AssetClass(data["asset_class"]),
            amount=float(data["amount"]),
            name=data.get("name"),
            cnpj=data.get("cnpj"),
        )


class WalletFile:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[WalletEntry]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [WalletEntry.from_dict(item) for item in data["assets"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"Wallet file {self._path} is not valid: {exc}") from exc

    def save(self, entries: Iterable[WalletEntry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        body = {"assets": [entry.to_dict() for entry in entries]}
        # Write then rename, so a crash never leaves a half-written wallet behind.
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def upsert(self, entry: WalletEntry) -> None:
        """Add an asset, or replace it in place if it is already in the wallet."""
        entries = self.load()
        for index, existing in enumerate(entries):
            if existing.identifier == entry.identifier:
                entries[index] = entry
                break
        else:
            entries.append(entry)
        self.save(entries)

    def remove(self, identifier: str) -> None:
        self.save(entry for entry in self.load() if entry.identifier != identifier)

    def replace_tickers(self, entries: Iterable[WalletEntry]) -> None:
        """Swap every entry added by ticker for ``entries``; funds added by CNPJ are kept."""
        self.save([*entries, *(entry for entry in self.load() if not entry.by_ticker)])


class ManualPortfolioProvider:
    """The wallet file as a Portfolio: shares valued at ``quote(ticker)``, funds as typed in."""

    INSTITUTION = "Manual"

    def __init__(self, wallet: WalletFile, quote: Callable[[str], float | None]) -> None:
        self._wallet = wallet
        self._quote = quote

    def fetch_portfolio(self) -> Portfolio:
        holdings: list[Holding] = []
        errors: list[str] = []
        for entry in self._wallet.load():
            name = entry.name or entry.identifier
            if not entry.by_ticker:
                holdings.append(
                    Holding(
                        name, entry.asset_class, self.INSTITUTION, entry.amount, cnpj=entry.cnpj
                    )
                )
                continue
            try:
                price = self._quote(entry.identifier)
            except InvestReporterError as exc:
                price = None
                errors.append(f"{entry.identifier}: no quote ({exc})")
            else:
                if price is None:
                    errors.append(f"{entry.identifier}: no quote available")
            holdings.append(
                Holding(
                    name,
                    entry.asset_class,
                    self.INSTITUTION,
                    market_value=entry.amount * price if price else 0.0,
                    ticker=entry.identifier,
                    cnpj=entry.cnpj,
                    quantity=entry.amount,
                    unit_price=price,
                )
            )
        return Portfolio(holdings=tuple(holdings), errors=tuple(errors))
