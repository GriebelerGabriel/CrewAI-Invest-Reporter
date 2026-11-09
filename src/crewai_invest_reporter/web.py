"""The wallet page: type in the stocks and funds you hold.

It has no login of its own and is meant for one person. Serve it on 127.0.0.1, and put it
behind a password (e.g. nginx basic auth) before making it reachable from anywhere else.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, url_for

from crewai_invest_reporter.domain.cnpj import cnpj_digits, format_cnpj
from crewai_invest_reporter.domain.errors import InvestReporterError
from crewai_invest_reporter.domain.models import AssetClass, AssetRef
from crewai_invest_reporter.providers.b3_position import read_b3_positions
from crewai_invest_reporter.providers.parsing import parse_br_number
from crewai_invest_reporter.providers.wallet import WalletEntry, WalletFile

logger = logging.getLogger(__name__)

SHORT_LABELS = {
    AssetClass.STOCK: "Ação",
    AssetClass.REAL_ESTATE_FUND: "FII",
    AssetClass.ETF: "ETF",
    AssetClass.BDR: "BDR",
    AssetClass.MUTUAL_FUND: "Fundo",
}
_UNITS = {AssetClass.STOCK: "ações", AssetClass.BDR: "BDRs"}  # FIIs and ETFs: "cotas"
_CLASS_ORDER = list(AssetClass)
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # a B3 position file is a few dozen KB


def create_app(
    wallet: WalletFile,
    resolve: Callable[[str], AssetRef],
    suggestions: Callable[[], list[tuple[str, str]]] = list,
) -> Flask:
    """``resolve`` identifies a typed ticker/CNPJ; ``suggestions`` feeds the autocompletion."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.jinja_env.filters.update(br=format_br, qty=format_quantity, asset_id=display_id)
    app.jinja_env.globals.update(
        short_label=SHORT_LABELS.get, unit=lambda entry: _UNITS.get(entry.asset_class, "cotas")
    )

    @app.before_request
    def reject_cross_site_posts():
        # Browsers resend a saved password with form posts from any site: accept only ours.
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and urlsplit(origin).netloc != request.host:
            abort(403)

    def page(error: str | None = None, status: int = 200, prefill: dict | None = None):
        entries = sorted(
            wallet.load(), key=lambda e: (_CLASS_ORDER.index(e.asset_class), e.identifier)
        )
        if prefill is None:
            editing = next((e for e in entries if e.identifier == request.args.get("editar")), None)
            prefill = _prefill(editing) if editing else {}
        try:
            options = suggestions()
        except InvestReporterError as exc:
            logger.warning("No ticker suggestions: %s", exc)
            options = []
        html = render_template(
            "wallet.html",
            entries=entries,
            suggestions=options,
            error=error,
            prefill=prefill,
            saved=request.args.get("salvo"),
            removed=request.args.get("removido"),
            imported=request.args.get("importados"),
        )
        return html, status

    @app.get("/")
    def index():
        return page()

    @app.post("/add")
    def add():
        form = {key: request.form.get(key, "").strip() for key in ("identifier", "amount", "name")}
        form["kind"] = "fund" if request.form.get("kind") == "fund" else "ticker"
        is_fund = form["kind"] == "fund"
        amount = parse_br_number(form["amount"])

        error = None
        if amount is None or amount <= 0:
            error = f"Informe {'um valor' if is_fund else 'uma quantidade'} maior que zero."
        elif is_fund and not cnpj_digits(form["identifier"]):
            error = "Digite um CNPJ válido, como 11.222.333/0001-81."
        elif not is_fund and cnpj_digits(form["identifier"]):
            error = "Isso é um CNPJ: cadastre fundos em “Fundo de investimento”, pelo valor em R$."
        if error:
            return page(error, 400, form)
        try:
            asset = resolve(form["identifier"])
        except InvestReporterError:
            return page(
                f"Não reconhecemos “{form['identifier']}”. Use um ticker da B3, como PETR4.",
                400,
                form,
            )
        name = form["name"] or asset.name
        wallet.upsert(WalletEntry(asset.identifier, asset.asset_class, amount, name, asset.cnpj))
        return redirect(url_for("index", salvo=asset.identifier))

    @app.post("/remove")
    def remove():
        identifier = request.form.get("identifier", "")
        wallet.remove(identifier)
        return redirect(url_for("index", removido=identifier))

    @app.post("/import")
    def import_b3():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return page("Escolha o arquivo .xlsx baixado da Área do Investidor da B3.", 400)
        try:
            positions = read_b3_positions(upload.read())
            assets = [(resolve(ticker), quantity) for ticker, quantity in positions.items()]
        except InvestReporterError as exc:
            logger.warning("B3 import failed: %s", exc)
            return page(
                "Não consegui ler esse arquivo. Envie a posição em Excel (.xlsx) baixada da "
                "Área do Investidor da B3.",
                400,
            )
        if not assets:
            return page("Nenhuma ação, FII, ETF ou BDR encontrada nesse arquivo.", 400)
        wallet.replace_tickers(
            WalletEntry(asset.identifier, asset.asset_class, quantity, asset.name, asset.cnpj)
            for asset, quantity in assets
        )
        return redirect(url_for("index", importados=len(assets)))

    @app.errorhandler(413)
    def upload_too_large(_error):
        return page("Arquivo grande demais: o limite é 5 MB.", 413)

    return app


def _prefill(entry: WalletEntry) -> dict:
    """Form values to edit an entry already in the wallet."""
    return {
        "kind": "ticker" if entry.by_ticker else "fund",
        "identifier": display_id(entry.identifier),
        "amount": format_quantity(entry.amount) if entry.by_ticker else format_br(entry.amount),
        "name": entry.name or "",
        "editing": True,
    }


def format_br(value: float, decimals: int = 2) -> str:
    """1234.5 -> "1.234,50" (Brazilian thousands and decimal separators)."""
    return f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def format_quantity(value: float) -> str:
    """Whole numbers without decimals: 1000 -> "1.000", 2.5 -> "2,50"."""
    return format_br(value, 0 if float(value).is_integer() else 2)


def display_id(identifier: str) -> str:
    """Tickers as they are, fund CNPJs formatted: "11.222.333/0001-81"."""
    return format_cnpj(identifier) if identifier.isdigit() else identifier
