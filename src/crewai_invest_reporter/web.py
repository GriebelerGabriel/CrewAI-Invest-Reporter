"""The public sign-up page: pick stocks, leave an email, confirm through the emailed link.

Links in emails only show a button; the action itself is a POST, because mailbox security
scanners open links (GET) on their own and would otherwise confirm or unsubscribe people.
Rate limiting belongs to the reverse proxy in front (see README "Daily email").
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from urllib.parse import urlsplit

from flask import Flask, abort, render_template, request

from crewai_invest_reporter.domain.errors import InvestReporterError, SubscriptionError
from crewai_invest_reporter.services.subscriptions import SubscriptionService

logger = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
        "frame-ancestors 'none'; base-uri 'none'"
    ),
    # Confirmation/unsubscribe tokens are in the URL: never leak them to other sites.
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def create_app(
    subscriptions: SubscriptionService,
    choices: Callable[[], list[tuple[str, str]]],
    contact_email: str = "",
) -> Flask:
    """``choices`` gives the (ticker, company name) pairs offered on the form."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    app.jinja_env.globals["contact_email"] = contact_email

    @app.before_request
    def reject_cross_site_posts():
        # Mail providers' one-click unsubscribe POSTs carry no Origin and are accepted.
        origin = request.headers.get("Origin")
        if request.method == "POST" and origin and urlsplit(origin).netloc != request.host:
            abort(403)

    @app.after_request
    def security_headers(response):
        response.headers.update(SECURITY_HEADERS)
        return response

    def form(error: str | None = None, status: int = 200, email: str = "", chosen=()):
        html = render_template(
            "index.html", choices=choices(), error=error, email=email, chosen=set(chosen)
        )
        return html, status

    def message(title: str, text: str, status: int = 200):
        return render_template("message.html", title=title, text=text), status

    @app.get("/")
    def index():
        return form()

    @app.post("/inscrever")
    def subscribe():
        email = request.form.get("email", "")
        tickers = request.form.getlist("tickers")
        if request.form.get("website"):  # hidden field only bots fill in
            return message("Quase lá", "Confira seu e-mail para confirmar a inscrição.")
        try:
            subscriptions.request(email, tickers)
        except SubscriptionError as exc:
            return form(str(exc), 400, email, tickers)
        except InvestReporterError as exc:
            logger.error("Sign-up email failed: %s", exc)
            return message("Tente mais tarde", "Não conseguimos enviar o e-mail agora.", 503)
        # Same answer whether or not the address was already subscribed.
        return message(
            "Quase lá",
            "Enviamos um link de confirmação para o seu e-mail. Abra-o em até 2 dias para "
            "começar a receber o resumo.",
        )

    @app.get("/confirmar")
    def confirm_page():
        return render_template(
            "action.html",
            title="Confirmar inscrição",
            text="Clique para confirmar e começar a receber o resumo diário.",
            action="/confirmar",
            button="Confirmar",
            token=request.args.get("t", ""),
        )

    @app.post("/confirmar")
    def confirm():
        result = subscriptions.confirm(request.form.get("t", ""))
        if result is None:
            return message(
                "Link inválido ou expirado", "Faça a inscrição de novo na página inicial.", 400
            )
        when = (
            "O resumo de hoje já foi enviado para você."
            if result.sent_today
            else "O primeiro resumo chega no próximo dia útil, antes da abertura da B3."
        )
        tickers = ", ".join(result.subscriber.tickers)
        return message("Inscrição confirmada", f"Ações: {tickers}. {when}")

    @app.get("/cancelar")
    def unsubscribe_page():
        return render_template(
            "action.html",
            title="Cancelar inscrição",
            text="Você deixa de receber o resumo e o seu e-mail é apagado.",
            action="/cancelar",
            button="Cancelar inscrição",
            token=request.args.get("t", ""),
        )

    @app.post("/cancelar")
    def unsubscribe():
        # The form sends the token in the body; one-click unsubscribe keeps it in the URL.
        subscriptions.unsubscribe(request.form.get("t") or request.args.get("t", ""))
        return message(
            "Inscrição cancelada", "Você não receberá mais e-mails e seus dados foram apagados."
        )

    @app.get("/privacidade")
    def privacy():
        return render_template("privacy.html")

    return app
