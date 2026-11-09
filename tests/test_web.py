from __future__ import annotations

from datetime import date

import pytest

from crewai_invest_reporter.domain.models import Digest
from crewai_invest_reporter.providers.database import Database
from crewai_invest_reporter.services.subscriptions import SubscriptionService
from crewai_invest_reporter.web import create_app
from tests.test_subscriptions import FakeMailer

MON = date(2026, 11, 9)


@pytest.fixture
def env(tmp_path):
    db, mailer = Database(tmp_path / "r.db"), FakeMailer()
    service = SubscriptionService(
        db, mailer, lambda: ["PETR4", "VALE3"], "http://localhost", lambda: MON
    )
    app = create_app(service, lambda: [("PETR4", "PETROBRAS"), ("VALE3", "")], "dpo@x.com")
    return app.test_client(), db, mailer


def test_form_lists_the_stocks_with_security_headers(env):
    client, _, _ = env
    page = client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'value="PETR4"' in html and "PETROBRAS" in html and 'value="VALE3"' in html
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]


def test_full_flow_subscribe_confirm_unsubscribe(env):
    client, db, mailer = env
    db.save_digest(Digest(MON, "PETR4", "PETROBRAS", 38.45, None, "- Novidade."))
    answer = client.post("/inscrever", data={"email": "a@x.com", "tickers": ["PETR4"]})
    assert answer.status_code == 200 and "Quase lá" in answer.get_data(as_text=True)

    token = mailer.last_link("confirmar")
    # Opening the link (what a mail scanner does) shows a button but confirms nothing.
    assert token in client.get(f"/confirmar?t={token}").get_data(as_text=True)
    assert db.confirmed_subscribers() == []
    confirmed = client.post("/confirmar", data={"t": token}).get_data(as_text=True)
    assert "Inscrição confirmada" in confirmed and "já foi enviado" in confirmed
    assert mailer.sent[-1]["Subject"].startswith("Suas ações hoje")

    # Mail providers' one-click unsubscribe: POST with the token in the URL.
    unsubscribe = mailer.last_link("cancelar")
    one_click = client.post(f"/cancelar?t={unsubscribe}", data="List-Unsubscribe=One-Click")
    assert one_click.status_code == 200
    assert db.subscriber_by_email("a@x.com") is None


def test_invalid_form_is_shown_again_with_the_error(env):
    client, _, mailer = env
    page = client.post("/inscrever", data={"email": "a@x.com"})
    assert page.status_code == 400
    assert "pelo menos uma" in page.get_data(as_text=True)
    assert 'value="a@x.com"' in page.get_data(as_text=True)
    assert mailer.sent == []


def test_bots_filling_the_hidden_field_get_no_email(env):
    client, _, mailer = env
    data = {"email": "a@x.com", "tickers": ["PETR4"], "website": "spam"}
    assert client.post("/inscrever", data=data).status_code == 200
    assert mailer.sent == []


def test_cross_site_posts_are_rejected(env):
    client, _, _ = env
    data = {"email": "a@x.com", "tickers": ["PETR4"]}
    assert (
        client.post("/inscrever", data=data, headers={"Origin": "https://evil.example"}).status_code
        == 403
    )


def test_mail_failure_gives_a_friendly_error(env):
    client, _, mailer = env
    mailer.fail_for.add("a@x.com")
    page = client.post("/inscrever", data={"email": "a@x.com", "tickers": ["PETR4"]})
    assert page.status_code == 503


def test_privacy_page_shows_the_contact(env):
    client, _, _ = env
    assert "dpo@x.com" in client.get("/privacidade").get_data(as_text=True)
