"""The emails' text (pt-BR, plain text: it reads well everywhere and is rarely flagged)."""

from __future__ import annotations

from datetime import date
from email.message import EmailMessage

from crewai_invest_reporter.domain.models import Digest

NEWS_LINKS = 5

DISCLAIMER = (
    "Resumo automático, gerado por IA a partir de notícias e documentos públicos da CVM. "
    "Não é recomendação de investimento."
)


def format_br(value: float, decimals: int = 2) -> str:
    """1234.5 -> "1.234,50" (Brazilian thousands and decimal separators)."""
    return f"{value:,.{decimals}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def confirmation_email(to: str, tickers: tuple[str, ...], confirm_url: str) -> EmailMessage:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = "Confirme sua inscrição no resumo diário de ações"
    message.set_content(
        f"Olá!\n\nRecebemos um pedido para enviar a este e-mail, todo dia útil antes da "
        f"abertura da B3, um resumo das novidades de: {', '.join(tickers)}.\n\n"
        f"Para confirmar, abra o link abaixo:\n{confirm_url}\n\n"
        "Se não foi você, ignore esta mensagem: sem confirmação, nada será enviado e o seu "
        "e-mail é apagado em 7 dias.\n"
    )
    return message


def digest_email(to: str, day: date, digests: list[Digest], unsubscribe_url: str) -> EmailMessage:
    with_news = [d for d in digests if d.summary]
    quiet = [d for d in digests if not d.summary]
    parts = [f"Novidades de {day:%d/%m/%Y} das ações que você acompanha.\n"]
    for digest in with_news:
        parts.append(_stock_section(digest))
    if quiet:
        parts.append("Sem novidades: " + "; ".join(_quiet_line(d) for d in quiet) + "\n")
    parts.append(f"--\n{DISCLAIMER}\nCancelar inscrição e apagar seus dados: {unsubscribe_url}\n")

    message = EmailMessage()
    message["To"] = to
    message["Subject"] = f"Suas ações hoje, {day:%d/%m/%Y}: " + ", ".join(
        d.ticker for d in with_news or digests
    )
    # One-click unsubscribe (RFC 8058), which large mailbox providers expect from senders.
    message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content("\n".join(parts))
    return message


def _price(digest: Digest) -> str:
    if digest.price is None:
        return "preço não disponível"
    text = f"R$ {format_br(digest.price)}"
    if digest.price_change is not None:
        sign = "+" if digest.price_change > 0 else ""
        text += f" ({sign}{format_br(digest.price_change * 100)}% desde o resumo anterior)"
    return text


def _stock_section(digest: Digest) -> str:
    title = f"{digest.ticker} - {digest.name}" if digest.name else digest.ticker
    lines = [title.upper(), _price(digest), "", digest.summary or "", "", "Fontes:"]
    filings = [s for s in digest.sources if s.kind == "filing"]
    news = [s for s in digest.sources if s.kind == "news"][:NEWS_LINKS]
    lines.extend(f"- {source.title}: {source.url}" for source in [*filings, *news])
    return "\n".join(lines) + "\n"


def _quiet_line(digest: Digest) -> str:
    return f"{digest.ticker} {_price(digest)}"
