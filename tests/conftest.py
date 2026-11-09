from __future__ import annotations

import pytest

from crewai_invest_reporter import main

# Settings the app reads from the environment; a developer's .env must not leak into tests.
APP_SETTINGS = (
    "MODEL", "BIFROST_BASE_URL", "BIFROST_API_KEY", "OPENAI_API_KEY", "WATCHLIST_FILE",
    "DATABASE_FILE", "REPORTS_DIR", "CACHE_DIR", "PUBLIC_URL", "CONTACT_EMAIL", "SMTP_HOST",
    "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_SECURITY", "MAIL_FROM",
)  # fmt: skip


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Tests never read the developer's .env, and start from empty app settings."""
    monkeypatch.setattr(main, "load_dotenv", lambda *args, **kwargs: False)
    for name in APP_SETTINGS:
        monkeypatch.delenv(name, raising=False)
