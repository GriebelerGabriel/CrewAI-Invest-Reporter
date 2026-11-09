from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from crewai_invest_reporter.domain.models import Digest, Source
from crewai_invest_reporter.providers.database import Database

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
MON, TUE = date(2026, 11, 9), date(2026, 11, 10)


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "data" / "reporter.db")


def test_digests_round_trip_and_previous_lookup(db):
    monday = Digest(
        MON, "PETR4", "PETROBRAS", 38.45, None, "- lucro", (Source("Fato", "u1", "filing"),)
    )
    db.save_digest(monday)
    db.save_digest(Digest(TUE, "PETR4", "PETROBRAS", 39.0, 0.0143))
    db.save_digest(Digest(TUE, "VALE3", "VALE"))

    assert db.digest(MON, "PETR4") == monday
    assert [d.ticker for d in db.digests_for(TUE)] == ["PETR4", "VALE3"]
    assert db.last_digest("PETR4", before=TUE) == monday
    assert db.last_digest("PETR4", before=MON) is None
    assert db.company_names() == {"PETR4": "PETROBRAS", "VALE3": "VALE"}


def test_selection_only_changes_once_confirmed(db):
    db.request_confirmation("a@x.com", ("PETR4",), "c1", "u1", NOW)
    assert db.confirmed_subscribers() == []
    subscriber = db.confirm("c1", NOW, sent_after=NOW - timedelta(days=2))
    assert subscriber.tickers == ("PETR4",)
    assert [s.email for s in db.confirmed_subscribers()] == ["a@x.com"]

    # A new request keeps the confirmed selection and the unsubscribe token until confirmed.
    db.request_confirmation("a@x.com", ("VALE3", "BBAS3"), "c2", "ignored", NOW)
    assert db.subscriber_by_email("a@x.com").tickers == ("PETR4",)
    updated = db.confirm("c2", NOW, sent_after=NOW - timedelta(days=2))
    assert updated.tickers == ("VALE3", "BBAS3")
    assert updated.unsubscribe_token == "u1"
    assert db.confirm("c2", NOW, sent_after=NOW - timedelta(days=2)) is None  # used once


def test_expired_confirmation_is_refused(db):
    db.request_confirmation("a@x.com", ("PETR4",), "c1", "u1", NOW - timedelta(days=3))
    assert db.confirm("c1", NOW, sent_after=NOW - timedelta(days=2)) is None


def test_unsubscribe_deletes_subscriber_and_deliveries(db):
    db.request_confirmation("a@x.com", ("PETR4",), "c1", "u1", NOW)
    subscriber = db.confirm("c1", NOW, sent_after=NOW - timedelta(days=2))
    assert db.claim_delivery(subscriber.id, MON, NOW)
    assert db.delete_subscriber("u1")
    assert db.subscriber_by_email("a@x.com") is None
    assert not db.delete_subscriber("u1")


def test_deliveries_are_claimed_once_and_can_be_released(db):
    db.request_confirmation("a@x.com", ("PETR4",), "c1", "u1", NOW)
    subscriber = db.confirm("c1", NOW, sent_after=NOW - timedelta(days=2))
    assert db.claim_delivery(subscriber.id, MON, NOW)
    assert not db.claim_delivery(subscriber.id, MON, NOW)
    db.release_delivery(subscriber.id, MON)
    assert db.claim_delivery(subscriber.id, MON, NOW)


def test_unconfirmed_signups_are_purged_from_their_latest_confirmation_email(db):
    week_ago = NOW - timedelta(days=8)
    db.request_confirmation("old@x.com", ("PETR4",), "c1", "u1", week_ago)
    db.request_confirmation("again@x.com", ("PETR4",), "c2", "u2", week_ago)
    db.request_confirmation("again@x.com", ("PETR4",), "c3", "u3", NOW)  # asked for a new link
    assert db.delete_unconfirmed(NOW - timedelta(days=7)) == 1
    assert db.subscriber_by_email("old@x.com") is None
    assert db.subscriber_by_email("again@x.com") is not None


def test_file_is_created_on_first_use_only(tmp_path):
    path = tmp_path / "data" / "reporter.db"
    db = Database(path)
    assert not path.exists()
    assert db.digests_for(MON) == []
    assert path.exists()
