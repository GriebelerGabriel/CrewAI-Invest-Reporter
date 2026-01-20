from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import json
import re
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
        investidor10_data, investidor10_error = self._fetch_investidor10(ticker=ticker)

        combined = {
            "input_ticker": ticker,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source": {"investidor10": {"data": investidor10_data, "error": investidor10_error}},
            "fundamentals": (investidor10_data or {}).get("mapped") or {},
        }
        return str(combined)

    def _fetch_investidor10(self, ticker: str) -> tuple[dict[str, Any] | None, str | None]:
        papel = ticker.replace(".SA", "").upper()
        if not papel.isalnum():
            return None, "investidor10 supports only alphanumeric tickers"

        base_path = "fiis" if papel.endswith("11") else "acoes"
        urls = [
            f"https://investidor10.com.br/{base_path}/{papel.lower()}/",
        ]
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

                candidate_raw = self._investidor10_extract_from_html(candidate_resp.text)
                if not candidate_raw:
                    continue

                resp = candidate_resp
                url = candidate_url
                raw = candidate_raw
                break

            if resp is None or url is None:
                return None, f"investidor10 http status={last_status}"

            if not raw:
                return None, "investidor10 parse error: empty extracted data"

            mapped = {
                "source_url": url,
                "papel": papel,
                "raw": raw,
                "mapped": {
                    "currentPrice": self._to_float(raw.get("Preço")),
                    "trailingPE": self._to_float(raw.get("P/L")),
                    "priceToBook": self._to_float(raw.get("P/VP")),
                    "dividendYield": self._to_percent(raw.get("Dividend Yield")),
                    "dividendsLast12m": self._to_float(raw.get("Dividendos (12m)")),
                    "beta": None,
                },
            }

            return mapped, None
        except Exception as e:
            return None, f"investidor10 request/parse failed: {e}"

    def _investidor10_extract_from_html(self, html: str) -> dict[str, str]:
        soup = BeautifulSoup(html, "lxml")
        raw: dict[str, str] = {}

        faq_texts: list[str] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            txt = script.get_text(strip=True)
            if not txt:
                continue
            try:
                data = json.loads(txt)
            except Exception:
                continue

            objs: list[dict[str, Any]] = []
            if isinstance(data, dict):
                objs.append(data)
            elif isinstance(data, list):
                objs.extend([o for o in data if isinstance(o, dict)])

            for obj in objs:
                if obj.get("@type") != "FAQPage":
                    continue
                for q in obj.get("mainEntity", []) or []:
                    if not isinstance(q, dict):
                        continue
                    ans = q.get("acceptedAnswer")
                    if not isinstance(ans, dict):
                        continue
                    t = ans.get("text")
                    if isinstance(t, str) and t.strip():
                        faq_texts.append(t)

        page_text = soup.get_text("\n", strip=True)
        combined = "\n".join(faq_texts) + "\n" + page_text if faq_texts else page_text

        def get_currency(label: str, pattern: str) -> None:
            m = re.search(pattern, combined, flags=re.IGNORECASE | re.DOTALL)
            if m:
                raw[label] = m.group(1).strip()

        def get_number(label: str, pattern: str) -> None:
            m = re.search(pattern, combined, flags=re.IGNORECASE | re.DOTALL)
            if m:
                raw[label] = m.group(1).strip()

        def get_percent(label: str, pattern: str) -> None:
            m = re.search(pattern, combined, flags=re.IGNORECASE | re.DOTALL)
            if m:
                raw[label] = m.group(1).strip() + "%"

        get_currency("Preço", r"está cotad[oa]\s+a\s+R\$\s*([0-9\.]+,[0-9]{2})")
        get_percent(
            "Variação (12M)",
            r"variaç[aã]o\s+de\s*([\-\+]?[0-9\.]+,[0-9]{1,2}|[\-\+]?[0-9\.]+)\s*%",
        )
        get_number("P/L", r"P\s*/\s*L\s+de\s*([0-9\.]+,[0-9]{1,2}|[0-9\.]+)")
        get_number("P/VP", r"P\s*/\s*VP\s+de\s*([0-9\.]+,[0-9]{1,2}|[0-9\.]+)")
        get_percent("Dividend Yield", r"Dividend\s*Yield[^0-9%]*([0-9\.]+,[0-9]{1,2}|[0-9\.]+)\s*%")
        get_currency(
            "Dividendos (12m)",
            r"Nos\s+últimos\s+12\s+meses,\s+distribuiu\s+um\s+total\s+de\s*R\$\s*([0-9\.]+,[0-9]{2})",
        )
        mliq = re.search(
            r"Liquidez\s*Di[áa]ria\s*R\$\s*([0-9\.]+,[0-9]{2})\s*([MK])",
            combined,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if mliq:
            raw["Liquidez Diária"] = f"R$ {mliq.group(1)} {mliq.group(2)}"

        raw = {k: v for k, v in raw.items() if v}
        return raw

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
