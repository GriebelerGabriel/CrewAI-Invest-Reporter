from __future__ import annotations

from datetime import date

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import AssetClass, AssetRef, Filing, NewsItem
from crewai_invest_reporter.services.research import (
    ResearchCollector,
    excerpt,
    select_filings,
    strip_boilerplate,
)
from tests.fakes import FakeDocuments, FakeFilings, FakeFundamentals, FakeNews

TODAY = date(2026, 10, 17)
MXRF = AssetRef("MXRF11", AssetClass.REAL_ESTATE_FUND, "Maxi Renda", "97521225000125")


def _filing(title, category, day, n=0):
    return Filing(title, category, date(2026, 10, day), f"https://doc/{title}/{n}", "FNET")


FII_FILINGS = [
    _filing("Relatório Gerencial", "Relatórios", 2),
    _filing("Relatório Gerencial", "Relatórios", 1, 1),  # older: only the newest is read
    _filing("Fato Relevante", "Fato Relevante", 10),
    _filing("Fato Relevante", "Fato Relevante", 9, 1),
    _filing("Fato Relevante", "Fato Relevante", 8, 2),  # third one exceeds the rule limit
    _filing("Rendimentos e Amortizações", "Aviso aos Cotistas - Estruturado", 15),
    _filing("Informe Mensal Estruturado", "Informes Periódicos", 15),
    _filing("Ata de Assembleia", "Assembleia", 16),  # no rule matches
]


def test_selection_rules_are_deterministic_and_bounded():
    chosen = select_filings(list(reversed(FII_FILINGS)), AssetClass.REAL_ESTATE_FUND)
    assert [(f.title, f.delivered_on.day, rule.max_chars) for f, rule in chosen] == [
        ("Relatório Gerencial", 2, 15_000),
        ("Fato Relevante", 10, 5_000),
        ("Fato Relevante", 9, 5_000),
        ("Rendimentos e Amortizações", 15, 3_000),
        ("Informe Mensal Estruturado", 15, 5_000),
    ]
    assert select_filings(FII_FILINGS, AssetClass.REAL_ESTATE_FUND) == chosen
    assert select_filings(FII_FILINGS, AssetClass.ETF) == []


def test_stock_rule_prefers_press_release_over_newer_financial_statements():
    filings = [
        Filing(
            "ITR - Demonstrações Financeiras",
            "Dados Econômico-Financeiros",
            date(2026, 8, 26),
            "https://d/itr",
            "CVM",
        ),
        Filing(
            "Press-release - Resultados 2T26",
            "Dados Econômico-Financeiros",
            date(2026, 8, 6),
            "https://d/release",
            "CVM",
        ),
    ]
    chosen = select_filings(filings, AssetClass.STOCK)
    assert [f.url for f, _ in chosen] == ["https://d/release"]
    only_itr = select_filings(filings[:1], AssetClass.STOCK)
    assert [f.url for f, _ in only_itr] == ["https://d/itr"]


def test_excerpt_normalizes_whitespace_and_cuts_on_word_boundary():
    text, truncated = excerpt("  Lucro   de\xa0R$ 10 bi \n\n\n Receita subiu muito ", 1000)
    assert (text, truncated) == ("Lucro de R$ 10 bi\nReceita subiu muito", False)
    cut, truncated = excerpt("palavra " * 100, 50)
    assert truncated and len(cut) <= 50 and not cut.endswith(" ")


def _collector(**overrides):
    deps = {
        "news": FakeNews(),
        "fundamentals": FakeFundamentals(),
        "filings": FakeFilings(filings=FII_FILINGS),
        "documents": FakeDocuments(words=2000),
    }
    deps.update(overrides)
    return ResearchCollector(**deps)


def test_collects_news_fundamentals_and_selected_documents():
    documents = FakeDocuments(words=2000)
    dossier = _collector(documents=documents).collect(MXRF)

    assert [d.filing.title for d in dossier.documents][:1] == ["Relatório Gerencial"]
    assert len(dossier.documents) == 5
    assert dossier.documents[0].truncated
    assert len(dossier.documents[0].text) <= 15_000
    assert [f.title for f in dossier.available_filings] == [
        "Relatório Gerencial",
        "Fato Relevante",
        "Ata de Assembleia",
    ]
    assert dossier.fundamentals.ticker == "MXRF11"
    assert dossier.news[0].title == "Notícia sobre MXRF11"
    assert dossier.gaps == ()
    assert len(documents.calls) == 5


def test_failures_become_data_gaps_instead_of_errors():
    dossier = _collector(
        news=FakeNews(error=ProviderError("rss down")),
        fundamentals=FakeFundamentals(error=ProviderError("site down")),
        filings=FakeFilings(error=ProviderError("fnet down")),
    ).collect(MXRF)
    assert dossier.documents == ()
    joined = " | ".join(dossier.gaps)
    assert "rss down" in joined and "site down" in joined and "fnet down" in joined


def test_unreadable_documents_are_kept_with_their_error():
    dossier = _collector(documents=FakeDocuments(error=ProviderError("HTTP 500"))).collect(MXRF)
    assert all(d.error == "HTTP 500" for d in dossier.documents)
    assert dossier.to_dict()["documents"][0]["error"] == "HTTP 500"


def test_unlisted_funds_skip_market_data_and_search_news_by_name():
    fund = AssetRef("11222333000181", AssetClass.MUTUAL_FUND, "Fundo XP", "11222333000181")
    news, fundamentals = FakeNews(), FakeFundamentals()
    dossier = _collector(news=news, fundamentals=fundamentals, filings=FakeFilings([])).collect(
        fund
    )
    assert fundamentals.calls == []
    assert news.calls[0][0] == '"Fundo XP"'
    assert any("não aplicável" in gap for gap in dossier.gaps)


def test_bdr_news_also_searches_the_us_ticker():
    news = FakeNews(items=[])
    _collector(news=news, filings=FakeFilings([])).collect(
        AssetRef("AAPL34", AssetClass.BDR, foreign_ticker="AAPL")
    )
    assert [c[0] for c in news.calls] == ["AAPL34", "AAPL stock"]


def test_assets_without_official_filings_are_reported_as_a_gap():
    dossier = _collector(filings=FakeFilings([])).collect(AssetRef("BOVA11", AssetClass.ETF))
    assert dossier.documents == ()
    assert any("nenhuma fonte oficial" in gap for gap in dossier.gaps)


HEADER = "www.petrobras.com.br/ri Para mais informações: Relações com Investidores"
DISCLAIMER = "Este documento pode conter previsões segundo o significado da Seção 27A"


def test_strip_boilerplate_removes_repeated_headers_and_toc():
    fato = f"[página 1]\n{HEADER}\n{DISCLAIMER}\nPetrobras adquire blocos\n100%\n"
    fato += f"[página 2]\n{HEADER}\n{DISCLAIMER}\nBônus de R$ 232 milhões\n100%"
    release = f"[página 1]\n{HEADER.upper()}\nDestaques ............ 4\nLucro de R$ 32 bi"
    cleaned_fato, cleaned_release = strip_boilerplate([fato, release])

    assert HEADER not in cleaned_fato and DISCLAIMER not in cleaned_fato
    assert "Petrobras adquire blocos" in cleaned_fato
    assert "Bônus de R$ 232 milhões" in cleaned_fato
    assert cleaned_fato.count("100%") == 2  # short repeated values are kept
    assert cleaned_release == "Lucro de R$ 32 bi"  # header removed, TOC line dropped


def test_strip_boilerplate_tolerates_pdf_spacing_noise():
    a = "Os termos similares visam a identificar tais previsões\nFato A"
    b = "Os termo s similare s visam a identificar tais previsões\nFato B"
    assert strip_boilerplate([a, b]) == ["Fato A", "Fato B"]


def test_news_is_sorted_newest_first_with_unknown_dates_last():
    items = [
        NewsItem("antiga", "S", "Mon, 21 Sep 2026 10:00:00 GMT", "u1"),
        NewsItem("sem data", "S", "", "u2"),
        NewsItem("recente", "S", "Tue, 13 Oct 2026 08:00:00 GMT", "u3"),
    ]
    dossier = _collector(news=FakeNews(items=items)).collect(MXRF)
    assert [n.title for n in dossier.news] == ["recente", "antiga", "sem data"]


def test_strip_boilerplate_removes_forward_looking_notice_only_when_complete():
    notice = (
        "Este documento pode conter previsões segundo o significado da Seção 27A\n"
        "o leitor não deve se basear exclusivamente nas informações aqui con tidas.\n"
    )
    assert strip_boilerplate([notice + "Dividendos de R$ 1,35 por ação"]) == [
        "Dividendos de R$ 1,35 por ação"
    ]
    unterminated = "Este relatório pode conter previsões sobre o futuro.\nLucro de R$ 32 bi"
    assert strip_boilerplate([unterminated]) == [unterminated]
