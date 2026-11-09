"""Sign-ups and deliveries of the daily email.

* A sign-up (new, or a change of stocks) only takes effect once the link emailed to the
  address is opened, so nobody can subscribe or change someone else's email.
* On confirmation the day's email goes out at once if the digests are already built.
* Unsubscribing deletes the subscriber; unconfirmed sign-ups are deleted after a week.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from typing import Protocol

from crewai_invest_reporter.domain.errors import InvestReporterError, SubscriptionError
from crewai_invest_reporter.domain.models import Digest, Subscriber, today_in_brazil, utc_now
from crewai_invest_reporter.domain.tickers import normalize_ticker
from crewai_invest_reporter.providers.database import Database
from crewai_invest_reporter.services.emails import confirmation_email, digest_email

logger = logging.getLogger(__name__)

CONFIRMATION_VALID_FOR = timedelta(days=2)
# A new confirmation email for the same address is sent at most this often.
RESEND_AFTER = timedelta(minutes=10)
UNCONFIRMED_KEPT_FOR = timedelta(days=7)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Mailer(Protocol):
    def send(self, message: EmailMessage) -> None: ...


@dataclass(frozen=True)
class Confirmation:
    subscriber: Subscriber
    sent_today: bool  # False: the first email comes with the next daily run


class SubscriptionService:
    def __init__(
        self,
        db: Database,
        mailer: Mailer,
        tickers: Callable[[], list[str]],
        public_url: str,
        today: Callable[[], date] = today_in_brazil,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._db = db
        self._mailer = mailer
        self._tickers = tickers
        self._public_url = public_url
        self._today = today
        self._clock = clock

    def request(self, email: str, tickers: Iterable[str]) -> None:
        """Validate a sign-up and email its confirmation link (unless one was just sent)."""
        email = normalize_email(email)
        chosen = self._valid_selection(tickers)
        now = self._clock()
        existing = self._db.subscriber_by_email(email)
        sent_at = existing.confirmation_sent_at if existing else None
        if sent_at and now - sent_at < RESEND_AFTER:
            logger.info("Confirmation for %s sent recently, not resending", email)
            return
        token = secrets.token_urlsafe(32)
        self._db.request_confirmation(email, chosen, token, secrets.token_urlsafe(32), now)
        self._mailer.send(confirmation_email(email, chosen, self._url("confirmar", token)))

    def confirm(self, token: str) -> Confirmation | None:
        now = self._clock()
        subscriber = self._db.confirm(token, now, sent_after=now - CONFIRMATION_VALID_FOR)
        if subscriber is None:
            return None
        day = self._today()
        try:
            sent = self._deliver(subscriber, day, self._db.digests_for(day))
        except InvestReporterError as exc:
            logger.warning("Email to %s failed, the daily run retries: %s", subscriber.email, exc)
            sent = False
        return Confirmation(subscriber, sent)

    def unsubscribe(self, token: str) -> bool:
        return self._db.delete_subscriber(token)

    def send_daily(self, day: date) -> tuple[int, list[str]]:
        """Email the day's digests to every confirmed subscriber not served yet."""
        digests = self._db.digests_for(day)
        sent, failed = 0, []
        for subscriber in self._db.confirmed_subscribers():
            try:
                sent += self._deliver(subscriber, day, digests)
            except InvestReporterError as exc:
                logger.warning("Email to %s failed: %s", subscriber.email, exc)
                failed.append(subscriber.email)
        return sent, failed

    def purge_unconfirmed(self) -> int:
        return self._db.delete_unconfirmed(self._clock() - UNCONFIRMED_KEPT_FOR)

    def _deliver(self, subscriber: Subscriber, day: date, digests: list[Digest]) -> bool:
        mine = [d for d in digests if d.ticker in subscriber.tickers]
        if not mine or not self._db.claim_delivery(subscriber.id, day, self._clock()):
            return False
        unsubscribe_url = self._url("cancelar", subscriber.unsubscribe_token)
        try:
            self._mailer.send(digest_email(subscriber.email, day, mine, unsubscribe_url))
        except InvestReporterError:
            self._db.release_delivery(subscriber.id, day)
            raise
        return True

    def _valid_selection(self, tickers: Iterable[str]) -> tuple[str, ...]:
        offered = self._tickers()
        chosen = []
        for raw in tickers:
            try:
                ticker = normalize_ticker(raw)
            except InvestReporterError:
                ticker = None
            if ticker not in offered:
                raise SubscriptionError(f"{raw!r} não está entre as ações disponíveis.")
            if ticker not in chosen:
                chosen.append(ticker)
        if not chosen:
            raise SubscriptionError("Escolha pelo menos uma ação.")
        return tuple(sorted(chosen, key=offered.index))

    def _url(self, path: str, token: str) -> str:
        return f"{self._public_url}/{path}?t={token}"


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise SubscriptionError("Digite um e-mail válido.")
    return email
