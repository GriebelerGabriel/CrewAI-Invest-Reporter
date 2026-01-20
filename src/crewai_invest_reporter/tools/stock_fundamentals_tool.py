from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import unicodedata

import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

_CACHE: dict[str, dict[str, Any]] = {}


class StockFundamentalsToolInput(BaseModel):
    ticker: str = Field(..., description="Stock ticker. For B3, you can pass PETR4 or PETR4.SA")


class StockFundamentalsTool(BaseTool):
    name: str = "stock_fundamentals"
    description: str = (
        "Fetch stock fundamentals from StatusInvest. "
        "Returns key fundamentals (if available)."
    )
    args_schema: type[BaseModel] = StockFundamentalsToolInput

    def _run(self, ticker: str) -> str:
        statusinvest_data, statusinvest_error = self._fetch_statusinvest(ticker=ticker)

        combined = {
            "input_ticker": ticker,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source": {"statusinvest": {"data": statusinvest_data, "error": statusinvest_error}},
            "fundamentals": (statusinvest_data or {}).get("mapped") or {},
        }
        return str(combined)

    def _fetch_statusinvest(self, ticker: str) -> tuple[dict[str, Any] | None, str | None]:
        papel = ticker.replace(".SA", "").upper()
        if not papel.isalnum():
            return None, "statusinvest supports only alphanumeric tickers"

        paths = ["fundos-imobiliarios", "fiagros"] if papel.endswith("11") else ["acoes"]
        urls = [f"https://statusinvest.com.br/{path}/{papel.lower()}" for path in paths]
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        try:
            resp = None
            url = None
            raw = None
            last_status = None
            for candidate_url in urls:
                candidate_resp = requests.get(candidate_url, headers=headers, timeout=15)
                last_status = candidate_resp.status_code
                if candidate_resp.status_code != 200:
                    continue

                candidate_soup = BeautifulSoup(candidate_resp.text, "lxml")
                candidate_raw = {
                    "P/L": self._statusinvest_get_indicator_any(candidate_soup, ["P/L", "P / L"]),
                    "P/VP": self._statusinvest_get_indicator_any(candidate_soup, ["P/VP", "P/VPA", "P / VP", "P / VPA"]),
                    "Div. Yield": self._statusinvest_get_indicator_any(
                        candidate_soup,
                        ["D.Y", "DY", "Dividend Yield", "Div. Yield", "Div Yield"],
                    ),
                    "Dividendo": self._statusinvest_get_indicator_any(
                        candidate_soup,
                        ["Dividendo", "Dividendos", "Último dividendo", "Ultimo dividendo"],
                    ),
                    "Valor de mercado": self._statusinvest_get_indicator_any(
                        candidate_soup,
                        ["Valor de mercado", "Valor de Mercado", "Valor de mercado (R$)", "Market cap", "Market Cap"],
                    ),
                    "Liquidez diária": self._statusinvest_get_indicator_any(
                        candidate_soup,
                        ["Liquidez diária", "Liquidez diaria", "Liquidez", "Liquidez média diária", "Liquidez media diaria"],
                    ),
                    "Patrimônio líquido": self._statusinvest_get_indicator_any(
                        candidate_soup,
                        ["Patrimônio líquido", "Patrimonio liquido", "Patrimônio", "Patrimonio"],
                    ),
                    "Marg. Líquida": self._statusinvest_get_indicator_any(
                        candidate_soup,
                        ["M. Líquida", "M. Liquida", "Margem Líquida", "Margem Liquida"],
                    ),
                }
                candidate_raw = {k: v for k, v in candidate_raw.items() if v}
                if not candidate_raw:
                    continue

                resp = candidate_resp
                url = candidate_url
                raw = candidate_raw
                break

            if resp is None or url is None:
                return None, f"statusinvest http status={last_status}"

            if not raw:
                return None, "statusinvest parse error: empty extracted data"

            mapped = {
                "source_url": url,
                "papel": papel,
                "raw": raw,
                "mapped": {
                    "trailingPE": self._to_float(raw.get("P/L")),
                    "priceToBook": self._to_float(raw.get("P/VP")),
                    "dividendYield": self._to_percent(raw.get("Div. Yield")),
                    "lastDividend": self._to_float(raw.get("Dividendo")),
                    "marketCap": self._to_int(raw.get("Valor de mercado")),
                    "averageDailyLiquidity": self._to_int(raw.get("Liquidez diária")),
                    "netAssets": self._to_int(raw.get("Patrimônio líquido")),
                    "profitMargins": self._to_percent(raw.get("Marg. Líquida")),
                    "beta": None,
                },
            }

            return mapped, None
        except Exception as e:
            return None, f"statusinvest request/parse failed: {e}"

    def _statusinvest_get_indicator(self, soup: BeautifulSoup, title: str) -> str | None:
        def _norm(s: str) -> str:
            s2 = unicodedata.normalize("NFKD", s)
            s2 = "".join(ch for ch in s2 if not unicodedata.combining(ch))
            s2 = s2.lower().strip()
            s2 = "".join(ch for ch in s2 if ch.isalnum())
            return s2

        wanted = _norm(title)
        h3 = None
        for tag in soup.find_all("h3"):
            txt = tag.get_text(" ", strip=True)
            if not txt:
                continue
            got = _norm(txt)
            if got == wanted or got.startswith(wanted):
                h3 = tag
                break
        if h3 is None:
            return None

        item = h3.find_parent("div", class_="item")
        if item is not None:
            val = item.find(class_=lambda c: c and "value" in c.split())
            if val is not None:
                out = val.get_text(" ", strip=True)
                return out or None

        container = h3.parent
        if container is None:
            return None

        val2 = container.find_next(class_=lambda c: c and "value" in c.split())
        if val2 is None:
            return None

        out2 = val2.get_text(" ", strip=True)
        return out2 or None

    def _statusinvest_get_indicator_any(self, soup: BeautifulSoup, titles: list[str]) -> str | None:
        for title in titles:
            out = self._statusinvest_get_indicator(soup, title)
            if out:
                return out
        return None

    def _to_float(self, s: str | None) -> float | None:
        if not s:
            return None
        cleaned = (
            s.replace(".", "")
            .replace("R$", "")
            .replace("%", "")
            .replace(" ", "")
            .replace("\xa0", "")
            .replace(",", ".")
        )
        try:
            return float(cleaned)
        except Exception:
            return None

    def _to_percent(self, s: str | None) -> float | None:
        val = self._to_float(s)
        if val is None:
            return None
        return val / 100.0

    def _to_int(self, s: str | None) -> int | None:
        val = self._to_float(s)
        if val is None:
            return None
        try:
            return int(val)
        except Exception:
            return None
