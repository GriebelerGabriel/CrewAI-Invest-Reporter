"""Fund documents from B3's FundosNET (https://fnet.bmfbovespa.com.br).

Covers FIIs and other structured funds: relatórios gerenciais, informes mensais/trimestrais,
fatos relevantes, comunicados and avisos aos cotistas (dividends).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import requests

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import AssetRef, Filing
from crewai_invest_reporter.providers.http import BROWSER_HEADERS, build_session

BASE_URL = "https://fnet.bmfbovespa.com.br/fnet/publico"


class FundosNetFilingsProvider:
    SOURCE = "B3 FundosNET"

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 60.0,  # FundosNET is often slow to answer
        today: Callable[[], date] = date.today,
    ) -> None:
        self._session = session or build_session(BROWSER_HEADERS)
        self._timeout = timeout
        self._today = today

    @staticmethod
    def document_url(document_id: int | str) -> str:
        return f"{BASE_URL}/downloadDocumento?id={document_id}"

    def list_filings(self, asset: AssetRef, *, days: int = 180, limit: int = 20) -> list[Filing]:
        if not asset.cnpj:
            return []
        params = {
            "d": 0,
            "s": 0,
            "l": 50,
            "o[0][dataEntrega]": "desc",
            "cnpjFundo": asset.cnpj,
        }
        try:
            response = self._session.get(
                f"{BASE_URL}/pesquisarGerenciadorDocumentosDados",
                params=params,
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"FundosNET request failed: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(f"FundosNET returned HTTP {response.status_code}")
        try:
            rows = response.json().get("data") or []
        except ValueError as exc:
            raise ProviderError("FundosNET returned invalid JSON") from exc

        since = self._today() - timedelta(days=days)
        filings = [
            filing
            for row in rows
            if (filing := self._to_filing(row)) is not None and filing.delivered_on >= since
        ]
        return filings[:limit]

    def _to_filing(self, row: dict[str, Any]) -> Filing | None:
        if row.get("id") is None:
            return None
        try:
            delivered = datetime.strptime(row.get("dataEntrega", ""), "%d/%m/%Y %H:%M").date()
        except ValueError:
            return None
        category = (row.get("categoriaDocumento") or "").strip()
        doc_type = (row.get("tipoDocumento") or "").strip()
        title = " - ".join(
            part for part in (doc_type, (row.get("especieDocumento") or "").strip()) if part
        )
        return Filing(
            title=title or category,
            category=category,
            delivered_on=delivered,
            url=self.document_url(row["id"]),
            source=self.SOURCE,
            reference=row.get("dataReferencia") or None,
        )
