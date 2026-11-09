"""Open Finance integration through Pluggy (https://pluggy.ai).

Brazilian Open Finance APIs are only directly accessible to regulated institutions, so an
aggregator is needed. Pluggy exposes Nubank and NuInvest through Open Finance, and its free
"MeuPluggy" connector lets an individual share their own data with their own app.

* ``PluggyClient`` is a thin REST client (auth, API-key caching, pagination).
* ``PluggyPortfolioProvider`` maps the stocks and funds among Pluggy investments into domain
  ``Holding`` objects; fixed income, pension plans, COEs and options are skipped.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import requests

from crewai_invest_reporter.domain.cnpj import cnpj_digits
from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import AssetClass, Holding, Portfolio
from crewai_invest_reporter.domain.tickers import try_normalize_ticker
from crewai_invest_reporter.providers.http import build_session
from crewai_invest_reporter.settings import PluggySettings

# Pluggy API keys are valid for 2 hours; refresh a bit earlier to avoid edge expirations.
API_KEY_TTL_SECONDS = 2 * 60 * 60 - 5 * 60
PAGE_SIZE = 500

# None: an EQUITY subtype that is not researched. Types missing from _TYPE_TO_CLASS
# (FIXED_INCOME, SECURITY i.e. pension plans, COE...) are skipped too.
_SUBTYPE_TO_CLASS: dict[str, AssetClass | None] = {
    "STOCK": AssetClass.STOCK,
    "BDR": AssetClass.BDR,
    "REAL_ESTATE_FUND": AssetClass.REAL_ESTATE_FUND,
    "ETF_FUND": AssetClass.ETF,
    "DERIVATIVES": None,
    "OPTION": None,
}
_TYPE_TO_CLASS: dict[str, AssetClass] = {
    "EQUITY": AssetClass.STOCK,
    "ETF": AssetClass.ETF,
    "MUTUAL_FUND": AssetClass.MUTUAL_FUND,
}
_INACTIVE_STATUSES = frozenset({"TOTAL_WITHDRAWAL"})


class PluggyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        base_url: str = "https://api.pluggy.ai",
        session: requests.Session | None = None,
        timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url.rstrip("/")
        self._session = session or build_session({"Accept": "application/json"})
        self._timeout = timeout
        self._clock = clock
        self._api_key: str | None = None
        self._api_key_expires_at = 0.0

    def get_item(self, item_id: str) -> dict[str, Any]:
        return self._get(f"/items/{item_id}")

    def list_investments(self, item_id: str) -> list[dict[str, Any]]:
        return list(self._paginate("/investments", {"itemId": item_id}))

    def _paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            body = self._get(path, {**params, "page": page, "pageSize": PAGE_SIZE})
            yield from body.get("results", [])
            if page >= int(body.get("totalPages") or 1):
                return
            page += 1

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._request("GET", path, params=params)
        if response.status_code == 401:
            # The cached key may have been revoked early: refresh once and retry.
            self._api_key = None
            response = self._request("GET", path, params=params)
        return self._json_or_raise(response, path)

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        headers = {"X-API-KEY": self._get_api_key()}
        try:
            return self._session.request(
                method, f"{self._base_url}{path}", headers=headers, timeout=self._timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Pluggy request to {path} failed: {exc}") from exc

    def _get_api_key(self) -> str:
        if self._api_key and self._clock() < self._api_key_expires_at:
            return self._api_key
        try:
            response = self._session.post(
                f"{self._base_url}/auth",
                json={"clientId": self._client_id, "clientSecret": self._client_secret},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"Pluggy authentication failed: {exc}") from exc
        api_key = self._json_or_raise(response, "/auth").get("apiKey")
        if not api_key:
            raise ProviderError("Pluggy authentication returned no apiKey")
        self._api_key = api_key
        self._api_key_expires_at = self._clock() + API_KEY_TTL_SECONDS
        return api_key

    @staticmethod
    def _json_or_raise(response: requests.Response, path: str) -> dict[str, Any]:
        if not response.ok:
            # Never echo request bodies: they may contain credentials.
            detail = response.text[:300] if response.text else ""
            raise ProviderError(f"Pluggy {path} returned HTTP {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(f"Pluggy {path} returned invalid JSON") from exc


class PluggyInvestmentMapper:
    """Translate Pluggy payloads into domain holdings (no I/O, easy to unit test)."""

    def to_holding(self, investment: dict[str, Any], fallback_institution: str) -> Holding | None:
        if investment.get("status") in _INACTIVE_STATUSES:
            return None
        market_value = _to_float(investment.get("balance"))
        if market_value is None:
            market_value = _to_float(investment.get("amount"))
        if not market_value:
            return None
        asset_class = self.asset_class(investment.get("type"), investment.get("subtype"))
        if asset_class is None:
            return None

        code = investment.get("code")
        ticker = try_normalize_ticker(code) if asset_class.is_listed else None
        institution = (investment.get("institution") or {}).get("name") or fallback_institution

        return Holding(
            name=investment.get("name") or ticker or "Sem nome",
            asset_class=asset_class,
            institution=institution,
            market_value=market_value,
            ticker=ticker,
            # For funds, Pluggy's "code" is the fund CNPJ.
            cnpj=None if asset_class.is_listed else cnpj_digits(code),
            quantity=_to_float(investment.get("quantity")),
            unit_price=_to_float(investment.get("value")),
            invested_amount=_to_float(investment.get("amountOriginal")),
            profit=_to_float(investment.get("amountProfit")),
            currency=investment.get("currencyCode") or "BRL",
        )

    @staticmethod
    def asset_class(type_: str | None, subtype: str | None) -> AssetClass | None:
        """The researched class of a Pluggy investment, or None to skip it."""
        if subtype and subtype in _SUBTYPE_TO_CLASS:
            return _SUBTYPE_TO_CLASS[subtype]
        return _TYPE_TO_CLASS.get(type_ or "")


class PluggyPortfolioProvider:
    """Consolidate the investments of every configured Pluggy item into one Portfolio."""

    def __init__(
        self,
        client: PluggyClient,
        item_ids: Iterable[str],
        mapper: PluggyInvestmentMapper | None = None,
    ) -> None:
        self._client = client
        self._item_ids = tuple(item_ids)
        self._mapper = mapper or PluggyInvestmentMapper()

    @classmethod
    def from_settings(
        cls, settings: PluggySettings, timeout: float = 30.0
    ) -> PluggyPortfolioProvider:
        settings.require()
        client = PluggyClient(
            settings.client_id, settings.client_secret, base_url=settings.base_url, timeout=timeout
        )
        return cls(client, settings.item_ids)

    def fetch_portfolio(self) -> Portfolio:
        holdings: list[Holding] = []
        errors: list[str] = []
        for item_id in self._item_ids:
            try:
                holdings.extend(self._fetch_item(item_id))
            except ProviderError as exc:
                # One broken connection must not hide the data from the other institutions.
                errors.append(f"item {item_id}: {exc}")
        if not holdings and errors:
            raise ProviderError("Could not load any Open Finance data. " + " | ".join(errors))
        return Portfolio(holdings=tuple(holdings), errors=tuple(errors))

    def _fetch_item(self, item_id: str) -> list[Holding]:
        item = self._client.get_item(item_id)
        institution = (item.get("connector") or {}).get("name") or "Desconhecida"

        return [
            holding
            for investment in self._client.list_investments(item_id)
            if (holding := self._mapper.to_holding(investment, institution)) is not None
        ]


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
