from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from crewai_invest_reporter.domain.errors import ProviderError, SubscriptionError
from crewai_invest_reporter.domain.models import Digest, Source
from crewai_invest_reporter.providers.database import Database
from crewai_invest_reporter.services.emails import digest_email
from crewai_invest_reporter.services.subscriptions import SubscriptionService

MON = date(2026, 11, 9)
WATCHLIST = ["PETR4", "BBAS3", "VALE3"]


class FakeMailer:
    def __init__(self):
        self.sent, self.fail_for = [], set()

    def send(self, message):
        if message["To"] in self.fail_for:
            raise ProviderError("smtp down")
        self.sent.append(message)

    def last_link(self, path):
        body = self.sent[-1].get_body(("plain",)).get_content()
        return next(w for w in body.split() if f"/{path}?t=" in w).split("t=")[1]


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 9, 11, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def env(tmp_path):
    db, mailer, clock = Database(tmp_path / "r.db"), FakeMailer(), Clock()
    service = SubscriptionService(
        db, mailer, lambda: WATCHLIST, "https://acoes.example", today=lambda: MON, clock=clock
    )
    return service, db, mailer, clock


def _digests(db):
    db.save_digest(
        Digest(MON, "PETR4", "PETROBRAS", 38.45, -0.012, "- Dividendos aprovados. [1]",
               (Source("Fato Relevante", "https://rad.cvm/1", "filing", cited=True),
                Source("Petrobras paga", "https://n/1"))),
    )  # fmt: skip
    db.save_digest(Digest(MON, "VALE3", "VALE", 61.2, 0.003))


def _subscribe(service, mailer, email="Ana@Example.com ", tickers=("vale3", "PETR4")):
    service.request(email, tickers)
    return service.confirm(mailer.last_link("confirmar"))


@pytest.mark.parametrize(
    ("email", "tickers", "message"),
    [
        ("not-an-email", ["PETR4"], "e-mail válido"),
        ("a@x.com", [], "pelo menos uma"),
        ("a@x.com", ["ITUB4"], "não está entre"),
    ],
)
def test_invalid_requests_are_refused(env, email, tickers, message):
    service, _, mailer, _ = env
    with pytest.raises(SubscriptionError, match=message):
        service.request(email, tickers)
    assert mailer.sent == []


def test_request_emails_a_confirmation_link_and_does_not_subscribe_yet(env):
    service, db, mailer, _ = env
    service.request(" Ana@Example.com", ["VALE3", "PETR4"])
    (message,) = mailer.sent
    assert message["To"] == "ana@example.com"
    assert "PETR4, VALE3" in message.get_content()  # watchlist order
    assert "https://acoes.example/confirmar?t=" in message.get_content()
    assert db.confirmed_subscribers() == []


def test_confirmation_is_not_resent_within_minutes(env):
    service, _, mailer, clock = env
    service.request("a@x.com", ["PETR4"])
    service.request("a@x.com", ["PETR4"])
    assert len(mailer.sent) == 1
    clock.now += timedelta(minutes=11)
    service.request("a@x.com", ["PETR4"])
    assert len(mailer.sent) == 2


def test_confirming_after_the_morning_run_sends_today_at_once(env):
    service, db, mailer, _ = env
    _digests(db)
    result = _subscribe(service, mailer)
    assert result.sent_today and result.subscriber.tickers == ("PETR4", "VALE3")
    email = mailer.sent[-1]
    assert email["To"] == "ana@example.com"
    assert email["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    # The daily run does not send the same day twice.
    assert service.send_daily(MON) == (0, [])


def test_confirming_before_the_morning_run_waits_for_it(env):
    service, db, mailer, _ = env
    result = _subscribe(service, mailer)
    assert not result.sent_today and len(mailer.sent) == 1  # just the confirmation
    _digests(db)
    assert service.send_daily(MON) == (1, [])


def test_unknown_or_expired_confirmation_links_fail(env):
    service, _, mailer, clock = env
    assert service.confirm("forged") is None
    service.request("a@x.com", ["PETR4"])
    clock.now += timedelta(days=3)
    assert service.confirm(mailer.last_link("confirmar")) is None


def test_failed_email_is_retried_by_the_next_run(env):
    service, db, mailer, _ = env
    _subscribe(service, mailer, "a@x.com", ["PETR4"])
    _digests(db)
    mailer.fail_for.add("a@x.com")
    assert service.send_daily(MON) == (0, ["a@x.com"])
    mailer.fail_for.clear()
    assert service.send_daily(MON) == (1, [])


def test_unsubscribe_link_deletes_the_subscriber(env):
    service, db, mailer, _ = env
    _digests(db)
    _subscribe(service, mailer)
    token = mailer.last_link("cancelar")
    assert service.unsubscribe(token)
    assert db.subscriber_by_email("ana@example.com") is None


def test_digest_email_shows_only_cited_sources_in_text_and_html():
    summary = "- Lucro de R$ 32 bi. [1]\n- Dividendos <altos>. [1, 2]"
    digests = [
        Digest(MON, "PETR4", "PETROBRAS", 38.45, -0.012, summary,
               (Source("Release 3T26", "https://rad.cvm/1", "filing", cited=True),
                Source("Petrobras paga", "https://n/1?a=1&b=2", cited=True),
                Source("Lula embarca", "https://n/unrelated"))),
        Digest(MON, "VALE3", "VALE", 61.2, 0.003),
    ]  # fmt: skip
    message = digest_email("a@x.com", MON, digests, "https://acoes.example/cancelar?t=abc")
    assert message["Subject"] == "Suas ações hoje, 09/11/2026: PETR4"
    assert message["List-Unsubscribe"] == "<https://acoes.example/cancelar?t=abc>"

    text = message.get_body(("plain",)).get_content()
    assert "R$ 38,45 (-1,20% desde o resumo anterior)" in text
    assert "[1] Release 3T26 (CVM): https://rad.cvm/1" in text
    assert "[2] Petrobras paga: https://n/1?a=1&b=2" in text
    assert "unrelated" not in text  # seen, not cited: not shown
    assert "Sem novidades: VALE3 R$ 61,20 (+0,30% desde o resumo anterior)" in text
    assert "Não é recomendação de investimento" in text

    html = message.get_body(("html",)).get_content()
    assert '<a href="https://n/1?a=1&amp;b=2">Petrobras paga</a>' in html
    assert "Dividendos &lt;altos&gt;." in html  # escaped
    assert '[<a href="https://rad.cvm/1"' in html  # citations are links
    assert "unrelated" not in html
    assert 'href="https://acoes.example/cancelar?t=abc"' in html
