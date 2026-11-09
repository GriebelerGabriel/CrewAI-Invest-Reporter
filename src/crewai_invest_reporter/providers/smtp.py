"""Send email through any SMTP server (a transactional email service, or a local relay)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.settings import MailSettings


class SmtpMailer:
    def __init__(self, settings: MailSettings, timeout: float = 30.0) -> None:
        self._settings = settings
        self._timeout = timeout

    def send(self, message: EmailMessage) -> None:
        config = self._settings
        message["From"] = config.sender
        message["Date"] = formatdate(localtime=False)
        domain = parseaddr(config.sender)[1].rpartition("@")[2] or None
        message["Message-ID"] = make_msgid(domain=domain)
        try:
            with self._open() as smtp:
                if config.username:
                    smtp.login(config.username, config.password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise ProviderError(f"Could not send email to {message['To']}: {exc}") from exc

    def _open(self) -> smtplib.SMTP:
        config = self._settings
        if config.security == "ssl":
            context = ssl.create_default_context()
            return smtplib.SMTP_SSL(
                config.host, config.port, timeout=self._timeout, context=context
            )
        smtp = smtplib.SMTP(config.host, config.port, timeout=self._timeout)
        if config.security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        return smtp
