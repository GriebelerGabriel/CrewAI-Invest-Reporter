from __future__ import annotations

from datetime import date

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import Filing, NewsItem, Stock
from crewai_invest_reporter.services.research import (
    ResearchCollector,
    excerpt,
    select_filings,
    strip_boilerplate,
)
from tests.fakes import FakeDocuments, FakeFilings, FakeFundamentals, FakeNews

PETR = Stock("PETR4", "PETROBRAS", "33000167000101")


def _filing(title, category, day, n=0):
    return Filing(title, category, date(2026, 10, day), f"https://doc/{title}/{n}", "CVM (IPE)")


FILINGS = [
    _filing("Press-release 3T26", "Dados Econômico-Financeiros", 2),
    _filing("ITR - Demonstrações Financeiras", "Dados Econômico-Financeiros", 5),  # not preferred
    _filing("Fato Relevante", "Fato Relevante", 10),
    _filing("Fato Relevante", "Fato Relevante", 9, 1),
    _filing("Fato Relevante", "Fato Relevante", 8, 2),  # third one exceeds the rule limit
    _filing("Juros sobre capital próprio", "Aviso aos Acionistas", 15),
    _filing("Novo diretor", "Comunicado ao Mercado", 15),
    _filing("Ata", "Reunião da Administração", 16),  # no rule matches
]


def test_selection_rules_are_deterministic_and_bounded():
    chosen = select_filings(list(reversed(FILINGS)))
    assert [(f.title, f.delivered_on.day, rule.max_chars) for f, rule in chosen] == [
        ("Press-release 3T26", 2, 15_000),
        ("Fato Relevante", 10, 5_000),
        ("Fato Relevante", 9, 5_000),
        ("Juros sobre capital próprio", 15, 3_000),
        ("Novo diretor", 15, 3_000),
    ]
    assert select_filings(FILINGS) == chosen


def test_results_rule_prefers_press_release_over_newer_financial_statements():
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
    assert [f.url for f, _ in select_filings(filings)] == ["https://d/release"]
    assert [f.url for f, _ in select_filings(filings[:1])] == ["https://d/itr"]


def test_excerpt_normalizes_whitespace_and_cuts_on_word_boundary():
    text, truncated = excerpt("  Lucro   de\xa0R$ 10 bi \n\n\n Receita subiu muito ", 1000)
    assert (text, truncated) == ("Lucro de R$ 10 bi\nReceita subiu muito", False)
    cut, truncated = excerpt("palavra " * 100, 50)
    assert truncated and len(cut) <= 50 and not cut.endswith(" ")


def _collector(**overrides):
    deps = {
        "news": FakeNews(),
        "fundamentals": FakeFundamentals(),
        "filings": FakeFilings(filings=FILINGS),
        "documents": FakeDocuments(words=2000),
    }
    deps.update(overrides)
    return ResearchCollector(**deps)


def test_collects_news_fundamentals_and_selected_documents():
    documents = FakeDocuments(words=2000)
    dossier = _collector(documents=documents).collect(PETR)

    assert dossier.documents[0].filing.title == "Press-release 3T26"
    assert len(dossier.documents) == 5
    assert dossier.documents[0].truncated
    assert len(dossier.documents[0].text) <= 15_000
    assert [f.title for f in dossier.available_filings] == [
        "ITR - Demonstrações Financeiras",
        "Fato Relevante",
        "Ata",
    ]
    assert dossier.fundamentals.ticker == "PETR4"
    assert dossier.news[0].title == "Notícia sobre PETR4"
    assert dossier.gaps == ()
    assert len(documents.calls) == 5


def test_failures_become_data_gaps_instead_of_errors():
    dossier = _collector(
        news=FakeNews(error=ProviderError("rss down")),
        fundamentals=FakeFundamentals(error=ProviderError("site down")),
        filings=FakeFilings(error=ProviderError("cvm down")),
    ).collect(PETR)
    assert dossier.documents == ()
    assert dossier.gaps == (
        "Notícias: rss down",
        "Fundamentos: site down",
        "Documentos oficiais: cvm down",
    )


def test_unreadable_documents_are_kept_with_their_error():
    dossier = _collector(documents=FakeDocuments(error=ProviderError("HTTP 500"))).collect(PETR)
    assert all(d.error == "HTTP 500" for d in dossier.documents)
    assert dossier.to_dict()["documents"][0]["error"] == "HTTP 500"


def test_stock_without_cnpj_skips_filings_with_a_gap():
    filings = FakeFilings()
    dossier = _collector(filings=filings).collect(Stock("PETR4"))
    assert filings.calls == []
    assert any("CNPJ da empresa desconhecido" in gap for gap in dossier.gaps)


def test_no_recent_filings_is_reported_as_a_gap():
    dossier = _collector(filings=FakeFilings([])).collect(PETR)
    assert dossier.documents == ()
    assert any("nenhum documento na CVM" in gap for gap in dossier.gaps)


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
    dossier = _collector(news=FakeNews(items=items)).collect(PETR)
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


def test_collect_new_keeps_only_unseen_items_and_reads_new_filings():
    news_items = [
        NewsItem("já enviada", "S", "", "https://n/old"),
        NewsItem("nova", "S", "", "https://n/new"),
    ]
    news, filings = FakeNews(items=news_items), FakeFilings(filings=FILINGS)
    seen = frozenset({"https://n/old", FILINGS[0].url})
    dossier = _collector(news=news, filings=filings).collect_new(PETR, days=3, seen=seen)

    assert [n.title for n in dossier.news] == ["nova"]
    assert news.calls == [("PETR4", 15, 3)]
    assert filings.calls == [("PETR4", 3, 50)]
    # Up to 3 new filings, each cut to the daily budget; the seen one is skipped.
    assert [d.filing.url for d in dossier.documents] == [f.url for f in FILINGS[1:4]]
    assert all(len(d.text) <= 4_000 for d in dossier.documents)
    assert dossier.fundamentals.ticker == "PETR4"
