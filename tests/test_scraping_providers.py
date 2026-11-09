from __future__ import annotations

import json

import pytest
import responses

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.providers.google_news import RSS_SEARCH_URL, GoogleNewsProvider
from crewai_invest_reporter.providers.http import build_session
from crewai_invest_reporter.providers.investidor10 import Investidor10Parser, Investidor10Provider

FAQ = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    "mainEntity": [
        {
            "@type": "Question",
            "acceptedAnswer": {
                "@type": "Answer",
                "text": (
                    "Hoje PETR4 está cotada a R$ 38,45, com variação de -3,20% em 12 meses. "
                    "Possui P/L de 4,5 e P/VP de 1,10. Dividend Yield de 14,2%. "
                    "Nos últimos 12 meses, distribuiu um total de R$ 5,46 por ação."
                ),
            },
        }
    ],
}
HTML = f"""
<html><head><script type="application/ld+json">{json.dumps(FAQ)}</script></head>
<body><div>Liquidez Diária R$ 1.234,56 M</div></body></html>
"""

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>Petrobras anuncia dividendos</title><link>https://n/1</link>
<pubDate>Mon, 13 Oct 2026 10:00:00 GMT</pubDate><source url="https://v">Valor</source></item>
<item><title>Quanto ganharia quem investiu em PETR4</title><link>https://n/2</link></item>
<item><title>Resultado do 3T26</title><link>https://n/3</link></item>
</channel></rss>"""


def test_investidor10_parser_extracts_indicators():
    raw = Investidor10Parser().parse(HTML)
    assert raw["Preço"] == "38,45"
    assert raw["Variação (12M)"] == "-3,20%"
    assert raw["P/L"] == "4,5"
    assert raw["P/VP"] == "1,10"
    assert raw["Dividend Yield"] == "14,2%"
    assert raw["Dividendos (12m)"] == "5,46"
    assert raw["Liquidez Diária"] == "R$ 1.234,56 M"


def test_investidor10_parser_returns_empty_for_unrelated_page():
    assert Investidor10Parser().parse("<html><body>nada</body></html>") == {}


@responses.activate
def test_investidor10_fetches_the_stock_page():
    responses.get("https://investidor10.com.br/acoes/brbi11/", body=HTML)
    fundamentals = Investidor10Provider().fetch("brbi11.sa")
    assert fundamentals.ticker == "BRBI11"
    assert fundamentals.source_url == "https://investidor10.com.br/acoes/brbi11/"
    assert fundamentals.metrics["currentPrice"] == 38.45
    assert fundamentals.metrics["dividendYield"] == pytest.approx(0.142)
    assert fundamentals.metrics["priceChange12m"] == pytest.approx(-0.032)


@responses.activate
def test_investidor10_rejects_pages_without_indicators():
    responses.get("https://investidor10.com.br/acoes/petr4/", body="<html>nada</html>")
    with pytest.raises(ProviderError, match="no indicators"):
        Investidor10Provider().fetch("PETR4")


@responses.activate
def test_investidor10_raises_when_no_page_has_data():
    responses.get("https://investidor10.com.br/acoes/petr4/", status=503)
    with pytest.raises(ProviderError, match="HTTP 503"):
        Investidor10Provider(session=build_session(retries=0)).fetch("PETR4")


@responses.activate
def test_google_news_filters_low_signal_titles_and_limits_results():
    responses.get(RSS_SEARCH_URL, body=RSS, content_type="application/rss+xml")
    items = GoogleNewsProvider().search("PETR4", max_results=5)
    assert [item.title for item in items] == ["Petrobras anuncia dividendos", "Resultado do 3T26"]
    assert items[0].source == "Valor"

    limited = GoogleNewsProvider().search("PETR4", max_results=1)
    assert len(limited) == 1


def test_google_news_url_is_properly_encoded():
    url = GoogleNewsProvider.build_url("PETR4 Petrobras", 7)
    assert "q=PETR4+Petrobras+when%3A7d" in url
    assert "ceid=BR%3Apt-BR" in url


@responses.activate
def test_google_news_wraps_http_errors():
    responses.get(RSS_SEARCH_URL, status=404)
    with pytest.raises(ProviderError):
        GoogleNewsProvider().search("PETR4")
