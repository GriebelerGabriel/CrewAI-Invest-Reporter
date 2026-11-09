"""SQLite storage: the day's digests, the subscribers, who already got which day's email,
and the verdicts of the full reports (to compare later with what the stocks really did).

Each method opens its own short connection, so the web server's threads and the daily job
(a separate process) can share the file; WAL mode lets readers and a writer work together.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path

from crewai_invest_reporter.domain.models import (
    Digest,
    Rating,
    Source,
    Subscriber,
    Verdict,
    utc_now,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS digests (
    day TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    price REAL,
    price_change REAL,
    summary TEXT,
    sources TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (day, ticker)
);
CREATE TABLE IF NOT EXISTS subscribers (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    tickers TEXT NOT NULL DEFAULT '',
    pending_tickers TEXT,
    confirm_token TEXT UNIQUE,
    unsubscribe_token TEXT NOT NULL UNIQUE,
    confirmation_sent_at TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    ticker TEXT NOT NULL,
    rating TEXT NOT NULL,
    probability_good INTEGER NOT NULL,
    probability_loss INTEGER NOT NULL,
    confidence TEXT NOT NULL,
    price REAL,
    metrics TEXT NOT NULL DEFAULT '{}',
    model TEXT NOT NULL DEFAULT '',
    report TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ratings_by_ticker ON ratings (ticker, day);
CREATE TABLE IF NOT EXISTS deliveries (
    subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (subscriber_id, day)
);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._ready = False  # the file is created on first use, not when wiring the app

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self._ready:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self._path, timeout=30)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(SCHEMA)
            self._ready = True
        conn = sqlite3.connect(self._path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    # --- digests -------------------------------------------------------------------------

    def save_digest(self, digest: Digest) -> None:
        sources = json.dumps([[s.title, s.url, s.kind] for s in digest.sources], ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO digests VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    digest.day.isoformat(),
                    digest.ticker,
                    digest.name,
                    digest.price,
                    digest.price_change,
                    digest.summary,
                    sources,
                ),
            )

    def digests_for(self, day: date) -> list[Digest]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM digests WHERE day = ? ORDER BY ticker", (day.isoformat(),)
            ).fetchall()
        return [_digest(row) for row in rows]

    def digest(self, day: date, ticker: str) -> Digest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM digests WHERE day = ? AND ticker = ?", (day.isoformat(), ticker)
            ).fetchone()
        return _digest(row) if row else None

    def last_digest(self, ticker: str, before: date) -> Digest | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM digests WHERE ticker = ? AND day < ? ORDER BY day DESC LIMIT 1",
                (ticker, before.isoformat()),
            ).fetchone()
        return _digest(row) if row else None

    def company_names(self) -> dict[str, str]:
        """Ticker -> company name, as of each ticker's latest digest."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ticker, name FROM digests WHERE name IS NOT NULL ORDER BY day"
            ).fetchall()
        return {row["ticker"]: row["name"] for row in rows}

    # --- ratings -------------------------------------------------------------------------

    def save_rating(self, rating: Rating) -> None:
        """Keep every run (several a day, or one per model, are all kept)."""
        verdict = rating.verdict
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ratings (day, ticker, rating, probability_good, probability_loss,
                    confidence, price, metrics, model, report, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rating.day.isoformat(),
                    rating.ticker,
                    verdict.rating,
                    verdict.probability_good,
                    verdict.probability_loss,
                    verdict.confidence,
                    rating.price,
                    json.dumps(rating.metrics),
                    rating.model,
                    rating.report,
                    utc_now().isoformat(),
                ),
            )

    def ratings_for(self, ticker: str) -> list[Rating]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ratings WHERE ticker = ? ORDER BY day, id", (ticker,)
            ).fetchall()
        return [_rating(row) for row in rows]

    # --- subscribers ---------------------------------------------------------------------

    def request_confirmation(
        self,
        email: str,
        tickers: tuple[str, ...],
        confirm_token: str,
        unsubscribe_token: str,
        now: datetime,
    ) -> None:
        """Store a selection awaiting confirmation; a confirmed selection stays until then."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscribers (email, pending_tickers, confirm_token,
                    unsubscribe_token, confirmation_sent_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (email) DO UPDATE SET
                    pending_tickers = excluded.pending_tickers,
                    confirm_token = excluded.confirm_token,
                    confirmation_sent_at = excluded.confirmation_sent_at
                """,
                (
                    email,
                    ",".join(tickers),
                    confirm_token,
                    unsubscribe_token,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

    def subscriber_by_email(self, email: str) -> Subscriber | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM subscribers WHERE email = ?", (email,)).fetchone()
        return _subscriber(row) if row else None

    def confirm(self, confirm_token: str, now: datetime, sent_after: datetime) -> Subscriber | None:
        """Apply the pending selection if the token is known and was sent after ``sent_after``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM subscribers WHERE confirm_token = ? AND confirmation_sent_at > ?",
                (confirm_token, sent_after.isoformat()),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE subscribers SET tickers = pending_tickers, pending_tickers = NULL,
                    confirm_token = NULL, confirmed_at = COALESCE(confirmed_at, ?)
                WHERE id = ?
                """,
                (now.isoformat(), row["id"]),
            )
            updated = conn.execute("SELECT * FROM subscribers WHERE id = ?", (row["id"],))
            return _subscriber(updated.fetchone())

    def delete_subscriber(self, unsubscribe_token: str) -> bool:
        """Forget the subscriber entirely (deliveries go too, by cascade)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM subscribers WHERE unsubscribe_token = ?", (unsubscribe_token,)
            )
            return cursor.rowcount > 0

    def confirmed_subscribers(self) -> list[Subscriber]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM subscribers WHERE confirmed_at IS NOT NULL ORDER BY id"
            ).fetchall()
        return [_subscriber(row) for row in rows]

    def delete_unconfirmed(self, created_before: datetime) -> int:
        """Drop sign-ups that were never confirmed, so their emails aren't kept."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM subscribers WHERE confirmed_at IS NULL AND created_at < ?",
                (created_before.isoformat(),),
            )
            return cursor.rowcount

    # --- deliveries ----------------------------------------------------------------------

    def claim_delivery(self, subscriber_id: int, day: date, now: datetime) -> bool:
        """Reserve a day's email for a subscriber; False if it was already sent (or claimed)."""
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO deliveries VALUES (?, ?, ?)",
                (subscriber_id, day.isoformat(), now.isoformat()),
            )
            return cursor.rowcount > 0

    def release_delivery(self, subscriber_id: int, day: date) -> None:
        """Undo a claim whose email could not be sent, so the next run retries it."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM deliveries WHERE subscriber_id = ? AND day = ?",
                (subscriber_id, day.isoformat()),
            )


def _digest(row: sqlite3.Row) -> Digest:
    return Digest(
        day=date.fromisoformat(row["day"]),
        ticker=row["ticker"],
        name=row["name"],
        price=row["price"],
        price_change=row["price_change"],
        summary=row["summary"],
        sources=tuple(Source(*item) for item in json.loads(row["sources"])),
    )


def _rating(row: sqlite3.Row) -> Rating:
    return Rating(
        day=date.fromisoformat(row["day"]),
        ticker=row["ticker"],
        verdict=Verdict(
            row["rating"], row["probability_good"], row["probability_loss"], row["confidence"]
        ),
        price=row["price"],
        metrics=json.loads(row["metrics"]),
        model=row["model"],
        report=row["report"],
    )


def _subscriber(row: sqlite3.Row) -> Subscriber:
    sent_at = row["confirmation_sent_at"]
    return Subscriber(
        id=row["id"],
        email=row["email"],
        tickers=tuple(t for t in row["tickers"].split(",") if t),
        unsubscribe_token=row["unsubscribe_token"],
        confirmed=row["confirmed_at"] is not None,
        confirmation_sent_at=datetime.fromisoformat(sent_at) if sent_at else None,
    )
