#!/usr/bin/env python
"""Command line interface.

Usage:
    python -m crewai_invest_reporter.main daily [--no-email]     # today's digests + the emails
    python -m crewai_invest_reporter.main web [--port 8000]      # the public sign-up page
    python -m crewai_invest_reporter.main report [PETR4 ...]     # full reports, rating saved
    python -m crewai_invest_reporter.main dossier PETR4 [--json] # what the agents read, no LLM
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
from crewai_invest_reporter.domain.models import ResearchDossier, today_in_brazil
from crewai_invest_reporter.settings import Settings
from crewai_invest_reporter.web import create_app

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")
logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crewai_invest_reporter",
        description="AI-agent investment reports for a fixed list of B3 stocks.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser(
        "daily", help="Build today's digest of every watchlist stock, then email subscribers"
    )
    daily.add_argument("--no-email", action="store_true", help="Build the digests only")

    web = sub.add_parser("web", help="Serve the sign-up page")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)

    report = sub.add_parser(
        "report",
        help="Full 4-agent report per stock (given tickers, or the whole watchlist); "
        "the verdict is saved to the database",
    )
    report.add_argument("tickers", nargs="*", metavar="TICKER")

    dossier = sub.add_parser(
        "dossier", help="Collect and show everything the agents would read (no LLM calls)"
    )
    dossier.add_argument("ticker")
    dossier.add_argument("--json", action="store_true", help="Print the full dossier as JSON")
    return parser


def _format_dossier(dossier: ResearchDossier) -> str:
    stock = dossier.stock
    lines = [
        f"{stock.display_name} | CNPJ {stock.cnpj or '-'}",
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


def _report(container: Container, tickers: list[str]) -> int:
    """One report per ticker; a failure is printed and the next ticker still runs."""
    failed = []
    for ticker in tickers:
        try:
            path = container.report_service.generate(ticker)
        except InvestReporterError as exc:
            print(f"{ticker}: error: {exc}", file=sys.stderr)
            failed.append(ticker)
        except Exception:  # an LLM or network failure must not stop the other stocks
            logger.exception("%s: report failed", ticker)
            failed.append(ticker)
        else:
            print(f"{ticker}: report written to {path}")
    if failed and len(tickers) > 1:
        print(f"Failed: {', '.join(failed)}", file=sys.stderr)
    return 1 if failed else 0


def _daily(container: Container, send: bool) -> int:
    day = today_in_brazil()
    tickers = container.tickers()
    container.settings.llm.validate()
    subscriptions = container.subscriptions if send else None  # checks the SMTP settings now
    failed = container.daily_digests.run(day, tickers)
    print(f"Digests for {day}: {len(tickers) - len(failed)} of {len(tickers)} stocks")
    if failed:
        print(f"Failed: {', '.join(failed)}", file=sys.stderr)
    if subscriptions is None:
        return 1 if failed else 0
    sent, failed_emails = subscriptions.send_daily(day)
    purged = subscriptions.purge_unconfirmed()
    print(f"Emails sent: {sent}, failed: {len(failed_emails)}, unconfirmed deleted: {purged}")
    return 1 if failed or failed_emails else 0


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    try:
        container = Container(Settings.from_env())
        if args.command == "daily":
            return _daily(container, send=not args.no_email)
        if args.command == "web":
            app = create_app(
                container.subscriptions, container.choices, container.settings.contact_email
            )
            serve(app, host=args.host, port=args.port)
        if args.command == "report":
            tickers = args.tickers or container.tickers()
            # Fail now rather than after minutes of data collection.
            container.settings.llm.validate()
            return _report(container, tickers)
        if args.command == "dossier":
            dossier = container.report_service.collect(args.ticker)
            if args.json:
                print(json.dumps(dossier.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(_format_dossier(dossier))
    except InvestReporterError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def run() -> None:
    """Console script entry point (see [project.scripts] in pyproject.toml)."""
    sys.exit(main())


if __name__ == "__main__":
    run()
