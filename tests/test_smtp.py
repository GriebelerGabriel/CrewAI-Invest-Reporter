from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import ClassVar

import pytest

from crewai_invest_reporter.domain.errors import ConfigurationError, ProviderError
from crewai_invest_reporter.providers import smtp as smtp_module
from crewai_invest_reporter.providers.smtp import SmtpMailer
from crewai_invest_reporter.settings import MailSettings


class FakeSMTP:
    instances: ClassVar[list[FakeSMTP]] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, message):
        if message["To"] == "fail@x.com":
            raise smtplib.SMTPRecipientsRefused({})
        self.calls.append(("send", message["From"], message["To"], bool(message["Message-ID"])))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtp_module.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtp_module.smtplib, "SMTP_SSL", FakeSMTP)


def _message(to="a@x.com"):
    message = EmailMessage()
    message["To"] = to
    message.set_content("oi")
    return message


SETTINGS = MailSettings("smtp.example", 587, "user", "secret", "starttls", "Resumo <r@ex.com>")


def test_sends_with_starttls_login_and_headers():
    SmtpMailer(SETTINGS).send(_message())
    (smtp,) = FakeSMTP.instances
    assert (smtp.host, smtp.port) == ("smtp.example", 587)
    assert smtp.calls == [
        "starttls",
        ("login", "user"),
        ("send", "Resumo <r@ex.com>", "a@x.com", True),
    ]


def test_failures_become_provider_errors():
    with pytest.raises(ProviderError, match=r"fail@x\.com"):
        SmtpMailer(SETTINGS).send(_message("fail@x.com"))


def test_missing_settings_fail_clearly():
    with pytest.raises(ConfigurationError, match="SMTP_HOST, MAIL_FROM"):
        MailSettings().require()
    with pytest.raises(ConfigurationError, match="SMTP_SECURITY"):
        MailSettings("h", sender="s", security="tls").require()
