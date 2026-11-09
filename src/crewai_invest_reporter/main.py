#!/usr/bin/env python
"""Command line interface.

Usage:
    python -m crewai_invest_reporter.main asset PETR4            # stock, FII, ETF, BDR or CNPJ
    python -m crewai_invest_reporter.main dossier MXRF11 [--json] # what the agents read, no LLM
    python -m crewai_invest_reporter.main holdings [--json]      # your stocks and funds, no LLM
    python -m crewai_invest_reporter.main web [--port 8000]      # the wallet page
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from collections.abc import Sequence

from dotenv import load_dotenv
from waitress import serve

from crewai_invest_reporter.bootstrap import Container
from crewai_invest_reporter.domain.errors import InvestReporterError
from crewai_invest_reporter.domain.models import Portfolio, ResearchDossier
from crewai_invest_reporter.settings import Settings
from crewai_invest_reporter.web import create_app

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crewai_invest_reporter",
        description="Investment reports for the stocks and funds in your wallet.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    asset = sub.add_parser(
        "asset", help="Generate a report for one asset: B3 ticker (stock/FII/ETF/BDR) or fund CNPJ"
    )
    asset.add_argument("identifier")

    dossier = sub.add_parser(
        "dossier", help="Collect and show everything the agents would read (no LLM calls)"
    )
    dossier.add_argument("identifier")
    dossier.add_argument("--json", action="store_true", help="Print the full dossier as JSON")

    holdings = sub.add_parser(
        "holdings", help="List your stocks and funds, from WALLET_SOURCE (no LLM calls)"
    )
    holdings.add_argument("--json", action="store_true", help="Print the holdings as JSON")

    web = sub.add_parser("web", help="Serve the wallet page, to type in your stocks and funds")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    return parser


def _format_holdings(portfolio: Portfolio) -> str:
    lines = [f"{'Ativo':<32} {'Classe':<18} {'Instituição':<16} {'Valor (R$)':>14} {'%':>7}"]
    total = portfolio.total_value
    for holding in sorted(portfolio.holdings, key=lambda h: h.market_value, reverse=True):
        weight = holding.market_value / total * 100 if total else 0.0
        lines.append(
            f"{(holding.ticker or holding.name)[:32]:<32} {holding.asset_class.value:<18} "
            f"{holding.institution[:16]:<16} {holding.market_value:>14,.2f} {weight:>6.2f}%"
        )
    lines.append(f"{'TOTAL':<68} {total:>14,.2f}")
    lines.extend(f"! {error}" for error in portfolio.errors)
    return "\n".join(lines)


def _format_dossier(dossier: ResearchDossier) -> str:
    asset = dossier.asset
    lines = [
        f"{asset.display_name} | {asset.asset_class.value} | CNPJ {asset.cnpj or '-'}"
        + (f" | US ticker {asset.foreign_ticker}" if asset.foreign_ticker else ""),
        "",
        f"Official documents read ({len(dossier.documents)}):",
    ]
    for doc in dossier.documents:
        f = doc.filing
        status = f"ERROR {doc.error}" if doc.error else f"{len(doc.text)} chars"
        status += " (truncated)" if doc.truncated else ""
        lines.append(f"  {f.delivered_on}  {f.category[:30]:<30} {f.title[:50]:<50} {status}")
        lines.append(f"              {f.url}")
    lines.append(f"Other recent filings listed: {len(dossier.available_filings)}")
    fundamentals = dossier.fundamentals
    if fundamentals:
        metrics = ", ".join(f"{k}={v}" for k, v in fundamentals.metrics.items() if v is not None)
        lines.append(f"Fundamentals: {metrics or 'none'}  [{fundamentals.source_url}]")
    else:
        lines.append("Fundamentals: none")
    lines.append(f"News items: {len(dossier.news)}")
    lines.extend(f"  - {item.title[:100]}" for item in dossier.news[:5])
    if dossier.gaps:
        lines.append("Data gaps:")
        lines.extend(f"  ! {gap}" for gap in dossier.gaps)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    try:
        container = Container(Settings.from_env())
        if args.command == "asset":
            # Fail now rather than after minutes of data collection.
            container.settings.llm.validate()
            path = container.asset_service.generate(args.identifier)
            print(f"Report written to {path}")
        elif args.command == "dossier":
            dossier = container.asset_service.collect(args.identifier)
            if args.json:
                print(json.dumps(dossier.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(_format_dossier(dossier))
        elif args.command == "holdings":
            portfolio = container.portfolio_provider().fetch_portfolio()
            if args.json:
                print(json.dumps(portfolio.to_dict(), ensure_ascii=False, indent=2))
            elif not portfolio.holdings:
                print("No holdings yet: add them on the wallet page (`web` command).")
            else:
                print(_format_holdings(portfolio))
        elif args.command == "web":
            app = create_app(
                container.wallet, container.resolver.resolve, container.asset_suggestions
            )
            serve(app, host=args.host, port=args.port)
    except InvestReporterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def run() -> None:
    """Console script entry point (see [project.scripts] in pyproject.toml)."""
    sys.exit(main())


if __name__ == "__main__":
    run()
