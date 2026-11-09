"""The emails (pt-BR). The daily one has a plain-text and an HTML version; the HTML one has
clickable titles instead of long links. Styles are inline: most mail apps ignore <style>."""

from __future__ import annotations

import re
from datetime import date
from email.message import EmailMessage
from html import escape

from crewai_invest_reporter.domain.models import Digest, Source

DISCLAIMER = (
    "Resumo automático, gerado por IA a partir de notícias e documentos públicos da CVM. "
    "Não é recomendação de investimento."
)
MUTED = "color:#5d6470"
_CITATION = re.compile(r"\[(\d+(?:, \d+)*)\]")


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
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = f"Suas ações hoje, {day:%d/%m/%Y}: " + ", ".join(
        d.ticker for d in with_news or digests
    )
    # One-click unsubscribe (RFC 8058), which large mailbox providers expect from senders.
    message["List-Unsubscribe"] = f"<{unsubscribe_url}>"
    message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(_text(day, with_news, quiet, unsubscribe_url))
    message.add_alternative(_html(day, with_news, quiet, unsubscribe_url), subtype="html")
    return message


def _cited(digest: Digest) -> list[Source]:
    """The sources the briefing cites, in its [1], [2]... order."""
    return [s for s in digest.sources if s.cited]


def _source_title(source: Source) -> str:
    return f"{source.title} (CVM)" if source.kind == "filing" else source.title


def _price(digest: Digest) -> str:
    if digest.price is None:
        return "preço não disponível"
    text = f"R$ {format_br(digest.price)}"
    if digest.price_change is not None:
        sign = "+" if digest.price_change > 0 else ""
        text += f" ({sign}{format_br(digest.price_change * 100)}% desde o resumo anterior)"
    return text


def _heading(digest: Digest) -> str:
    return f"{digest.ticker} - {digest.name}" if digest.name else digest.ticker


# --- plain text ---------------------------------------------------------------------------


def _text(day: date, with_news: list[Digest], quiet: list[Digest], unsubscribe_url: str) -> str:
    parts = [f"Novidades de {day:%d/%m/%Y} das ações que você acompanha.\n"]
    for digest in with_news:
        lines = [_heading(digest).upper(), _price(digest), "", digest.summary or ""]
        if sources := _cited(digest):
            lines += ["", "Fontes:"]
            lines += [f"[{n}] {_source_title(s)}: {s.url}" for n, s in enumerate(sources, 1)]
        parts.append("\n".join(lines) + "\n")
    if quiet:
        parts.append("Sem novidades: " + "; ".join(f"{d.ticker} {_price(d)}" for d in quiet) + "\n")
    parts.append(f"--\n{DISCLAIMER}\nCancelar inscrição e apagar seus dados: {unsubscribe_url}\n")
    return "\n".join(parts)


# --- HTML ---------------------------------------------------------------------------------


def _html(day: date, with_news: list[Digest], quiet: list[Digest], unsubscribe_url: str) -> str:
    body = [f"<p>Novidades de {day:%d/%m/%Y} das ações que você acompanha.</p>"]
    body += [_html_section(digest) for digest in with_news]
    if quiet:
        items = "; ".join(f"<strong>{d.ticker}</strong> {escape(_price(d))}" for d in quiet)
        body.append(f'<p style="margin-top:24px">Sem novidades: {items}</p>')
    body.append(
        '<hr style="border:0;border-top:1px solid #d9dde3;margin:24px 0 12px">'
        f'<p style="font-size:12px;{MUTED}">{escape(DISCLAIMER)}<br>'
        f'<a href="{escape(unsubscribe_url)}" style="{MUTED}">Cancelar inscrição e apagar seus '
        "dados</a></p>"
    )
    return (
        '<!doctype html><html lang="pt-BR"><body style="margin:0;padding:16px;'
        'font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#1c1f24">'
        f'<div style="max-width:640px;margin:0 auto">{"".join(body)}</div></body></html>'
    )


def _html_section(digest: Digest) -> str:
    sources = _cited(digest)
    name = ""
    if digest.name:
        name = f' <span style="font-weight:normal;{MUTED}">{escape(digest.name)}</span>'
    parts = [
        f'<h2 style="font-size:18px;margin:28px 0 2px">{digest.ticker}{name}</h2>',
        f'<p style="margin:0;{MUTED}">{escape(_price(digest))}</p>',
        '<ul style="padding-left:20px;margin:10px 0">',
    ]
    for line in (digest.summary or "").splitlines():
        if line.strip():
            text = escape(line.strip().removeprefix("- "))
            parts.append(f'<li style="margin:4px 0">{_link_citations(text, sources)}</li>')
    parts.append("</ul>")
    if sources:
        links = "".join(
            f'<li><a href="{escape(s.url)}">{escape(_source_title(s))}</a></li>' for s in sources
        )
        parts.append(
            f'<p style="font-size:13px;margin:8px 0 0;{MUTED}">Fontes:</p>'
            f'<ol style="font-size:13px;padding-left:20px;margin:2px 0">{links}</ol>'
        )
    return "".join(parts)


def _link_citations(text: str, sources: list[Source]) -> str:
    """Make "[1, 2]" clickable: each number links to its source."""

    def link(match: re.Match[str]) -> str:
        numbers = []
        for part in match.group(1).split(", "):
            index = int(part) - 1
            if 0 <= index < len(sources):
                url = escape(sources[index].url)
                numbers.append(f'<a href="{url}" style="{MUTED}">{part}</a>')
        return f"[{', '.join(numbers)}]" if numbers else ""

    return _CITATION.sub(link, text)
