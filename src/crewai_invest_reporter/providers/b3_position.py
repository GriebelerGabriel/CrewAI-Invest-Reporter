"""Read the position spreadsheet exported from B3's Área do Investidor (``posicao-*.xlsx``).

The file has one tab per product (Ações, BDR, ETF, Fundo de Investimento for FIIs, Tesouro
Direto, Renda Fixa...). The tabs of listed assets have the columns "Código de Negociação"
and "Quantidade"; tabs without them are skipped, whatever their name. A ticker held at two
brokers is listed twice and summed. openpyxl parses the XML with defusedxml (installed).
"""

from __future__ import annotations

import io
import unicodedata
import zipfile
from collections import defaultdict

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.tickers import try_normalize_ticker
from crewai_invest_reporter.providers.parsing import parse_br_number

CODE_COLUMN = "codigo de negociacao"
QUANTITY_COLUMN = "quantidade"
_HEADER_SEARCH_ROWS = 10  # the header is the first row today; allow a few title rows


def read_b3_positions(content: bytes) -> dict[str, float]:
    """Ticker -> quantity held, summed across brokers."""
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise ProviderError(f"Not a B3 .xlsx position file: {exc}") from exc

    positions: dict[str, float] = defaultdict(float)
    for sheet in workbook.worksheets:
        columns = None
        for index, row in enumerate(sheet.iter_rows(values_only=True)):
            if columns is None:
                if index >= _HEADER_SEARCH_ROWS:
                    break
                columns = _find_columns(row)
                continue
            ticker = try_normalize_ticker(_text(row, columns[0]))
            quantity = _number(row, columns[1])
            if ticker and quantity and quantity > 0:  # skips "Total" and blank rows
                positions[ticker] += quantity
    workbook.close()
    return dict(positions)


def _find_columns(row: tuple) -> tuple[int, int] | None:
    names = [_normalize(cell) for cell in row]
    if CODE_COLUMN in names and QUANTITY_COLUMN in names:
        return names.index(CODE_COLUMN), names.index(QUANTITY_COLUMN)
    return None


def _normalize(cell: object) -> str:
    """Lowercase, unaccented, single-spaced: "Código de Negociação " -> "codigo de negociacao"."""
    text = unicodedata.normalize("NFKD", str(cell or "")).encode("ascii", "ignore").decode()
    return " ".join(text.lower().split())


def _text(row: tuple, index: int) -> str | None:
    value = row[index] if index < len(row) else None
    return str(value).strip() if value is not None else None


def _number(row: tuple, index: int) -> float | None:
    value = row[index] if index < len(row) else None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return parse_br_number(str(value)) if value is not None else None
