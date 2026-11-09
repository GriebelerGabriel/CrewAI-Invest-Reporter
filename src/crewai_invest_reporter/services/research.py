"""Deterministic research collection: what the agents read is decided here, in plain code.

For each asset the collector runs fixed queries against fixed sources, picks official
documents with explicit per-asset-class rules, and cuts each one to a fixed character
budget. No LLM chooses what to search or read, so runs are reproducible and auditable
(``main dossier <ticker>`` prints exactly what the agents receive).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from crewai_invest_reporter.domain.errors import InvestReporterError
from crewai_invest_reporter.domain.models import (
    AssetClass,
    AssetRef,
    DocumentExcerpt,
    Filing,
    Fundamentals,
    NewsItem,
    ResearchDossier,
)
from crewai_invest_reporter.domain.ports import (
    DocumentReader,
    FilingsProvider,
    FundamentalsProvider,
    NewsProvider,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SelectionRule:
    """Pick up to ``max_documents`` filings whose category/title contains a pattern.

    Filings whose text contains a ``prefer`` pattern come first, then newest first; e.g. a
    results press release is preferred over the full financial statements of a quarter.
    """

    label: str
    patterns: tuple[str, ...]
    max_documents: int
    max_chars: int
    prefer: tuple[str, ...] = ()

    def matches(self, filing: Filing) -> bool:
        return any(pattern in _haystack(filing) for pattern in self.patterns)

    def is_preferred(self, filing: Filing) -> bool:
        return any(pattern in _haystack(filing) for pattern in self.prefer)


def _haystack(filing: Filing) -> str:
    return f"{filing.category} {filing.title}".lower()


# Order matters: earlier rules have priority and each filing is used at most once.
SELECTION_RULES: dict[AssetClass, tuple[SelectionRule, ...]] = {
    AssetClass.STOCK: (
        SelectionRule(
            "Release de resultados",
            ("dados econômico-financeiros",),
            1,
            15_000,
            prefer=("press-release", "press release", "release de resultados", "desempenho"),
        ),
        SelectionRule("Fato relevante", ("fato relevante",), 2, 5_000),
        SelectionRule("Proventos", ("relatório proventos", "aviso aos acionistas"), 1, 3_000),
        SelectionRule("Comunicado ao mercado", ("comunicado ao mercado",), 1, 3_000),
    ),
    AssetClass.REAL_ESTATE_FUND: (
        SelectionRule("Relatório gerencial", ("relatório gerencial",), 1, 15_000),
        SelectionRule("Fato relevante", ("fato relevante",), 2, 5_000),
        SelectionRule("Rendimentos", ("rendimentos",), 1, 3_000),
        SelectionRule("Informe mensal", ("informe mensal",), 1, 5_000),
    ),
    AssetClass.MUTUAL_FUND: (
        SelectionRule("Relatório", ("relatório",), 1, 12_000),
        SelectionRule("Fato relevante", ("fato relevante",), 2, 4_000),
        SelectionRule("Informe", ("informe",), 1, 4_000),
    ),
    AssetClass.BDR: (
        SelectionRule("Earnings release", ("earnings release",), 1, 15_000),
        SelectionRule("Current report", ("current report",), 2, 3_000),
    ),
}

NEWS_DAYS = 30
NEWS_MAX_ITEMS = 15
FILINGS_DAYS = 180
OTHER_FILINGS_LISTED = 10


def select_filings(
    filings: list[Filing], asset_class: AssetClass
) -> list[tuple[Filing, SelectionRule]]:
    """Apply the asset class rules; within a rule, preferred titles first, then newest."""
    ordered = sorted(filings, key=lambda f: f.delivered_on, reverse=True)
    chosen: list[tuple[Filing, SelectionRule]] = []
    used: set[str] = set()
    for rule in SELECTION_RULES.get(asset_class, ()):
        picked = 0
        # Stable sort: preferred filings first, newest first within each group.
        for filing in sorted(ordered, key=lambda f: not rule.is_preferred(f)):
            if picked >= rule.max_documents:
                break
            if filing.url not in used and rule.matches(filing):
                chosen.append((filing, rule))
                used.add(filing.url)
                picked += 1
    return chosen


_PAGE_MARKER = re.compile(r"^\[página \d+\]$")
_TOC_LINE = re.compile(r"(\.\s?){5,}\s*\d+\s*$")  # "Destaques ........ 4"
_MIN_BOILERPLATE_KEY = 15  # short repeated lines may be real data (e.g. "100%")
# Standard Brazilian forward-looking-statements notice, from its opening to its closing
# phrase; nothing is removed unless both are found within 1,500 characters.
_FORWARD_LOOKING_NOTICE = re.compile(
    r"(?:Este|Esta|O presente)\s+(?:documento|relatório|apresentação|comunicado)\s+pode\s+"
    r"conter\s+previsões.{0,1500}?aqui\s+con\s?tidas\s*\.",
    re.IGNORECASE | re.DOTALL,
)


def _published_at(item: NewsItem) -> datetime:
    """RSS dates are RFC 822 ("Mon, 05 Oct 2026 07:00:00 GMT"); unknown dates sort last."""
    try:
        published = parsedate_to_datetime(item.published)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return published if published.tzinfo else published.replace(tzinfo=timezone.utc)


def _line_key(line: str) -> str:
    """Comparison key robust to PDF extraction noise ("termo s similares" vs "termos")."""
    return re.sub(r"\W+", "", line.lower())


def strip_boilerplate(texts: list[str]) -> list[str]:
    """Remove headers, footers and disclaimers that repeat across pages or documents.

    A long line is boilerplate when it shows up on two or more pages of one document, or in
    two or more documents of the same batch (e.g. an issuer's IR header and legal notice on
    every filing). The standard forward-looking-statements notice and table-of-contents lines
    are dropped too.
    """
    pages_per_doc = [_split_pages(_FORWARD_LOOKING_NOTICE.sub("", text)) for text in texts]
    page_hits: dict[str, int] = {}
    doc_hits: dict[str, int] = {}
    for pages in pages_per_doc:
        doc_keys: set[str] = set()
        for page in pages:
            page_keys = {_line_key(line) for line in page}
            page_keys = {key for key in page_keys if len(key) >= _MIN_BOILERPLATE_KEY}
            for key in page_keys:
                page_hits[key] = page_hits.get(key, 0) + 1
            doc_keys |= page_keys
        for key in doc_keys:
            doc_hits[key] = doc_hits.get(key, 0) + 1

    boilerplate = {key for key, hits in page_hits.items() if hits >= 2}
    cleaned = []
    for pages in pages_per_doc:
        kept = [
            line
            for page in pages
            for line in page
            if _line_key(line) not in boilerplate and not _TOC_LINE.search(line)
        ]
        cleaned.append("\n".join(kept))
    return cleaned


def _split_pages(text: str) -> list[list[str]]:
    pages: list[list[str]] = [[]]
    for raw in text.splitlines():
        line = raw.strip()
        if _PAGE_MARKER.match(line):
            pages.append([])
        elif line:
            pages[-1].append(line)
    return [page for page in pages if page]


def excerpt(text: str, max_chars: int) -> tuple[str, bool]:
    """Normalize whitespace and cut at a word boundary within ``max_chars``."""
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines())
    clean = "\n".join(line for line in lines if line)
    if len(clean) <= max_chars:
        return clean, False
    cut = clean.rfind(" ", 0, max_chars)
    return clean[: cut if cut > max_chars * 0.8 else max_chars], True


class ResearchCollector:
    def __init__(
        self,
        news: NewsProvider,
        fundamentals: FundamentalsProvider,
        filings: FilingsProvider,
        documents: DocumentReader,
    ) -> None:
        self._news = news
        self._fundamentals = fundamentals
        self._filings = filings
        self._documents = documents

    def collect(self, asset: AssetRef) -> ResearchDossier:
        gaps: list[str] = []
        news = self._collect_news(asset, gaps)
        fundamentals = self._collect_fundamentals(asset, gaps)
        documents, others = self._collect_documents(asset, gaps)
        return ResearchDossier(
            asset=asset,
            news=tuple(news),
            fundamentals=fundamentals,
            documents=tuple(documents),
            available_filings=tuple(others),
            gaps=tuple(gaps),
        )

    # --- news ------------------------------------------------------------------------------

    def _collect_news(self, asset: AssetRef, gaps: list[str]) -> list[NewsItem]:
        queries = self._news_queries(asset)
        if not queries:
            gaps.append("Notícias: sem ticker nem nome para pesquisar.")
            return []
        items: list[NewsItem] = []
        seen: set[str] = set()
        for query in queries:
            try:
                found = self._news.search(query, max_results=NEWS_MAX_ITEMS, days=NEWS_DAYS)
            except InvestReporterError as exc:
                gaps.append(f"Notícias ({query}): {exc}")
                continue
            for item in found:
                key = item.title.strip().lower()
                if key and key not in seen and len(items) < NEWS_MAX_ITEMS:
                    seen.add(key)
                    items.append(item)
        if not items:
            gaps.append(f"Notícias: nenhuma notícia nos últimos {NEWS_DAYS} dias.")
        return sorted(items, key=_published_at, reverse=True)

    @staticmethod
    def _news_queries(asset: AssetRef) -> list[str]:
        if asset.asset_class is AssetClass.MUTUAL_FUND:
            return [f'"{asset.name}"'] if asset.name else []
        queries = [asset.identifier]
        if asset.foreign_ticker:
            queries.append(f"{asset.foreign_ticker} stock")
        return queries

    # --- fundamentals ------------------------------------------------------------------------

    def _collect_fundamentals(self, asset: AssetRef, gaps: list[str]) -> Fundamentals | None:
        if not asset.asset_class.is_listed:
            gaps.append("Fundamentos de mercado: não aplicável (ativo não listado em bolsa).")
            return None
        try:
            return self._fundamentals.fetch(asset.identifier)
        except InvestReporterError as exc:
            gaps.append(f"Fundamentos: {exc}")
            return None

    # --- official documents --------------------------------------------------------------------

    def _collect_documents(
        self, asset: AssetRef, gaps: list[str]
    ) -> tuple[list[DocumentExcerpt], list[Filing]]:
        try:
            filings = self._filings.list_filings(asset, days=FILINGS_DAYS, limit=50)
        except InvestReporterError as exc:
            gaps.append(f"Documentos oficiais: {exc}")
            filings = []

        selected = select_filings(filings, asset.asset_class)
        documents = self._read_all([(filing, rule.max_chars) for filing, rule in selected])
        used = {filing.url for filing, _ in selected}
        others = [f for f in filings if f.url not in used][:OTHER_FILINGS_LISTED]

        if not filings:
            gaps.append(
                f"Documentos oficiais: nenhuma fonte oficial cobre "
                f"{asset.asset_class.value} ou não há documentos em {FILINGS_DAYS} dias."
            )
        return documents, others

    def _read_all(self, wanted: list[tuple[Filing, int]]) -> list[DocumentExcerpt]:
        """Read documents, strip shared boilerplate across the batch, then cut to budget."""
        texts: list[str | None] = []
        errors: list[str | None] = []
        for filing, _ in wanted:
            try:
                texts.append(self._documents.read(filing.url).text)
                errors.append(None)
            except InvestReporterError as exc:
                logger.warning("Could not read %s: %s", filing.url, exc)
                texts.append(None)
                errors.append(str(exc))

        readable = [text for text in texts if text is not None]
        cleaned = iter(strip_boilerplate(readable))
        excerpts = []
        for (filing, max_chars), text, error in zip(wanted, texts, errors, strict=True):
            if text is None:
                excerpts.append(DocumentExcerpt(filing, error=error))
                continue
            body, truncated = excerpt(next(cleaned), max_chars)
            if not body:
                body_error = "documento sem texto extraível (imagem/scan?)"
                excerpts.append(DocumentExcerpt(filing, error=body_error))
            else:
                excerpts.append(DocumentExcerpt(filing, text=body, truncated=truncated))
        return excerpts
