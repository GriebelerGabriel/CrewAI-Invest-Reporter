"""Application settings, read once from environment variables (and an optional ``.env``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from crewai_invest_reporter.domain.errors import ConfigurationError

# CrewAI's own default model, in the provider/model form Bifrost routes on.
DEFAULT_BIFROST_MODEL = "openai/gpt-4.1-mini"


@dataclass(frozen=True)
class LlmSettings:
    """Where the agents' LLM calls go.

    With ``BIFROST_BASE_URL`` set, every call goes through the Bifrost AI gateway, which
    serves all its providers (OpenAI, Anthropic, Gemini...) behind one OpenAI-compatible API
    and identifies this project by its virtual key (``BIFROST_API_KEY``). ``MODEL`` is then a
    ``provider/model`` name. Without it, CrewAI's own defaults apply (``OPENAI_API_KEY``,
    ``MODEL``).
    """

    model: str = ""
    bifrost_base_url: str = ""
    bifrost_api_key: str = ""

    @property
    def uses_bifrost(self) -> bool:
        return bool(self.bifrost_base_url)

    @property
    def bifrost_model(self) -> str:
        return self.model or DEFAULT_BIFROST_MODEL

    def validate(self) -> None:
        """Fail before any data is collected if the gateway settings cannot work."""
        if not self.uses_bifrost:
            return
        if not self.bifrost_api_key:
            raise ConfigurationError(
                "BIFROST_BASE_URL is set but BIFROST_API_KEY is missing: set it to this "
                "project's Bifrost virtual key. See README section 'LLM gateway (Bifrost)'."
            )
        if "/" not in self.bifrost_model:
            raise ConfigurationError(
                f"Bifrost model names are provider/model (e.g. {DEFAULT_BIFROST_MODEL}), "
                f"got {self.bifrost_model!r}. Check MODEL."
            )


SMTP_SECURITY = ("starttls", "ssl", "none")


@dataclass(frozen=True)
class MailSettings:
    """SMTP server the emails go out through (a transactional email service in production)."""

    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    # starttls (port 587), ssl (port 465) or none (a local test server only).
    security: str = "starttls"
    # From address, e.g. "Resumo de Ações <resumo@yourdomain.com>".
    sender: str = ""

    def require(self) -> MailSettings:
        required = (("SMTP_HOST", self.host), ("MAIL_FROM", self.sender))
        missing = [name for name, value in required if not value]
        if missing:
            raise ConfigurationError(
                f"Email is not configured. Missing env vars: {', '.join(missing)}. "
                "See README section 'Daily email'."
            )
        if self.security not in SMTP_SECURITY:
            raise ConfigurationError(f"SMTP_SECURITY must be one of {', '.join(SMTP_SECURITY)}")
        return self


def _default_cache_dir() -> Path:
    base = os.getenv("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "crewai_invest_reporter"


@dataclass(frozen=True)
class Settings:
    watchlist_file: Path = Path("watchlist.txt")
    reports_dir: Path = Path("reports")
    cache_dir: Path = field(default_factory=_default_cache_dir)
    http_timeout_seconds: float = 15.0
    database_file: Path = Path("reporter.db")
    # Address the sign-up page is reached at; links in the emails start with it.
    public_url: str = "http://127.0.0.1:8000"
    # Shown on the privacy page: where people can ask about their data.
    contact_email: str = ""
    llm: LlmSettings = field(default_factory=LlmSettings)
    mail: MailSettings = field(default_factory=MailSettings)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            watchlist_file=Path(os.getenv("WATCHLIST_FILE") or "watchlist.txt"),
            reports_dir=Path(os.getenv("REPORTS_DIR") or "reports"),
            cache_dir=Path(os.getenv("CACHE_DIR") or _default_cache_dir()).expanduser(),
            http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS") or "15"),
            database_file=Path(os.getenv("DATABASE_FILE") or "reporter.db"),
            public_url=(os.getenv("PUBLIC_URL") or "http://127.0.0.1:8000").strip().rstrip("/"),
            contact_email=os.getenv("CONTACT_EMAIL", "").strip(),
            llm=LlmSettings(
                model=os.getenv("MODEL", "").strip(),
                bifrost_base_url=os.getenv("BIFROST_BASE_URL", "").strip().rstrip("/"),
                bifrost_api_key=os.getenv("BIFROST_API_KEY", "").strip(),
            ),
            mail=MailSettings(
                host=os.getenv("SMTP_HOST", "").strip(),
                port=int(os.getenv("SMTP_PORT") or "587"),
                username=os.getenv("SMTP_USERNAME", "").strip(),
                password=os.getenv("SMTP_PASSWORD", ""),
                security=(os.getenv("SMTP_SECURITY") or "starttls").strip().lower(),
                sender=os.getenv("MAIL_FROM", "").strip(),
            ),
        )
