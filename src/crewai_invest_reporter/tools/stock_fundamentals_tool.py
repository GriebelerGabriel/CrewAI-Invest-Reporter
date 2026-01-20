from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

import requests
import yfinance as yf
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


class StockFundamentalsToolInput(BaseModel):
    ticker: str = Field(..., description="Stock ticker. For B3, you can pass PETR4 or PETR4.SA")
    period: str = Field("1y", description="Price history period (e.g. 6mo, 1y, 5y)")


class StockFundamentalsTool(BaseTool):
    name: str = "stock_fundamentals"
    description: str = (
        "Fetch stock fundamentals and recent market data using yfinance. "
        "Returns key fundamentals (if available) and price-based metrics (returns, volatility)."
    )
    args_schema: type[BaseModel] = StockFundamentalsToolInput

    def _run(self, ticker: str, period: str = "1y") -> str:
        cache_key = (ticker, period)
        if cache_key in _CACHE:
            return str(_CACHE[cache_key])

        yf_ticker = self._to_yfinance_ticker(ticker)
        yfinance_data, yfinance_error = self._fetch_yfinance(yf_ticker=yf_ticker, period=period)

        fundamentus_data, fundamentus_error = self._fetch_fundamentus(ticker=ticker)

        combined = {
            "input_ticker": ticker,
            "yfinance_ticker": yf_ticker,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "sources": {
                "yfinance": {"data": yfinance_data, "error": yfinance_error},
                "fundamentus": {"data": fundamentus_data, "error": fundamentus_error},
            },
            "best_effort": {
                "fundamentals": self._merge_best_effort_fundamentals(
                    yfinance_data, fundamentus_data
                ),
                "price_metrics": (yfinance_data or {}).get("price_metrics", {}),
            },
            "discrepancies": self._find_discrepancies(yfinance_data, fundamentus_data),
        }

        _CACHE[cache_key] = combined
        return str(combined)

    def _to_yfinance_ticker(self, ticker: str) -> str:
        yf_ticker = ticker
        if yf_ticker.isalnum() and len(yf_ticker) <= 6 and not yf_ticker.endswith(".SA"):
            yf_ticker = f"{yf_ticker}.SA"
        return yf_ticker

    def _fetch_yfinance(
        self, yf_ticker: str, period: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        last_error: str | None = None

        for attempt in range(1, 4):
            try:
                t = yf.Ticker(yf_ticker)

                info: dict[str, Any]
                try:
                    info = t.get_info() or {}
                except Exception as e:
                    info = {}
                    last_error = f"yfinance get_info error: {e}"

                hist = t.history(period=period)
                close = hist["Close"] if "Close" in hist.columns else None

                price_metrics: dict[str, Any] = {}
                if close is not None and len(close) >= 2:
                    first = float(close.iloc[0])
                    last = float(close.iloc[-1])
                    total_return = (last / first) - 1 if first != 0 else None

                    daily_ret = close.pct_change().dropna()
                    vol = float(daily_ret.std()) * math.sqrt(252) if len(daily_ret) > 2 else None

                    price_metrics = {
                        "period": period,
                        "first_close": first,
                        "last_close": last,
                        "total_return": total_return,
                        "annualized_volatility": vol,
                    }

                fundamentals = {
                    "symbol": info.get("symbol", yf_ticker),
                    "shortName": info.get("shortName"),
                    "sector": info.get("sector"),
                    "industry": info.get("industry"),
                    "country": info.get("country"),
                    "currency": info.get("currency"),
                    "marketCap": info.get("marketCap"),
                    "trailingPE": info.get("trailingPE"),
                    "forwardPE": info.get("forwardPE"),
                    "priceToBook": info.get("priceToBook"),
                    "dividendYield": info.get("dividendYield"),
                    "profitMargins": info.get("profitMargins"),
                    "beta": info.get("beta"),
                }

                return {"fundamentals": fundamentals, "price_metrics": price_metrics}, last_error
            except Exception as e:
                last_error = f"yfinance request failed (attempt {attempt}): {e}"
                time.sleep(1.5 * attempt)

        return None, last_error

    def _fetch_fundamentus(self, ticker: str) -> tuple[dict[str, Any] | None, str | None]:
        papel = ticker.replace(".SA", "").upper()
        if not papel.isalnum():
            return None, "fundamentus supports only alphanumeric tickers"

        url = f"https://fundamentus.com.br/detalhes.php?papel={papel}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                return None, f"fundamentus http status={resp.status_code}"

            soup = BeautifulSoup(resp.text, "lxml")
            tables = soup.find_all("table")
            if not tables:
                return None, "fundamentus parse error: no tables found"

            raw = self._parse_label_value_tables(soup)
            if not raw:
                return None, "fundamentus parse error: empty extracted data"

            mapped = {
                "source_url": url,
                "papel": papel,
                "raw": raw,
                "mapped": {
                    "trailingPE": self._to_float(raw.get("P/L")),
                    "priceToBook": self._to_float(raw.get("P/VP")),
                    "dividendYield": self._to_percent(raw.get("Div. Yield")),
                    "marketCap": self._to_int(raw.get("Valor de mercado")),
                    "profitMargins": self._to_percent(raw.get("Marg. Líquida")),
                    "beta": None,
                },
            }

            return mapped, None
        except Exception as e:
            return None, f"fundamentus request/parse failed: {e}"

    def _parse_label_value_tables(self, soup: BeautifulSoup) -> dict[str, str]:
        data: dict[str, str] = {}
        for td in soup.find_all("td"):
            cls = td.get("class") or []
            if "label" in cls:
                label = td.get_text(strip=True)
                val_td = td.find_next_sibling("td")
                if val_td is None:
                    continue
                val = val_td.get_text(" ", strip=True)
                if label and val:
                    data[label] = val
        return data

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

    def _merge_best_effort_fundamentals(
        self,
        yfinance_data: dict[str, Any] | None,
        fundamentus_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        yf_fund = (yfinance_data or {}).get("fundamentals", {})
        f_map = (fundamentus_data or {}).get("mapped") or {}
        merged = dict(yf_fund)

        for k, v in f_map.items():
            if merged.get(k) is None and v is not None:
                merged[k] = v

        return merged

    def _find_discrepancies(
        self,
        yfinance_data: dict[str, Any] | None,
        fundamentus_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        yf_fund = (yfinance_data or {}).get("fundamentals", {})
        f_map = (fundamentus_data or {}).get("mapped") or {}

        fields = ["trailingPE", "priceToBook", "dividendYield", "marketCap", "profitMargins"]
        out: dict[str, Any] = {}
        for field in fields:
            a = yf_fund.get(field)
            b = f_map.get(field)
            if a is None or b is None:
                continue
            try:
                delta = abs(float(a) - float(b))
            except Exception:
                continue
            if delta != 0:
                out[field] = {"yfinance": a, "fundamentus": b, "abs_delta": delta}
        return out
