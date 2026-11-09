"""Application settings, read once from environment variables (and an optional ``.env``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from crewai_invest_reporter.domain.errors import ConfigurationError


def _split_csv(value: str | None) -> tuple[str, ...]:
    return tuple(part.strip() for part in (value or "").split(",") if part.strip())


@dataclass(frozen=True)
class PluggySettings:
    """Credentials for Pluggy, the Open Finance aggregator used to read Nubank/NuInvest data."""

    client_id: str = ""
    client_secret: str = ""
    item_ids: tuple[str, ...] = ()
    base_url: str = "https://api.pluggy.ai"

    def require(self) -> PluggySettings:
        missing = [
            name
            for name, value in (
                ("PLUGGY_CLIENT_ID", self.client_id),
                ("PLUGGY_CLIENT_SECRET", self.client_secret),
                ("PLUGGY_ITEM_IDS", self.item_ids),
            )
            if not value
        ]
        if missing:
            raise ConfigurationError(
                "Open Finance is not configured. Missing env vars: "
                f"{', '.join(missing)}. See README section 'Open Finance (Nubank / NuInvest)', "
                "or set WALLET_SOURCE=manual to use the wallet page."
            )
        return self


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


def _default_cache_dir() -> Path:
    base = os.getenv("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "crewai_invest_reporter"


# Where holdings come from: the wallet page's file, or Open Finance through Pluggy.
WALLET_SOURCES = ("manual", "pluggy")


@dataclass(frozen=True)
class Settings:
    reports_dir: Path = Path("reports")
    cache_dir: Path = field(default_factory=_default_cache_dir)
    http_timeout_seconds: float = 15.0
    # SEC asks automated clients to identify themselves, ideally "Name contact@email".
    sec_user_agent: str = "crewai-invest-reporter/0.1"
    wallet_source: str = "manual"
    wallet_file: Path = Path("wallet.json")
    llm: LlmSettings = field(default_factory=LlmSettings)
    pluggy: PluggySettings = field(default_factory=PluggySettings)

    def __post_init__(self) -> None:
        if self.wallet_source not in WALLET_SOURCES:
            raise ConfigurationError(f"WALLET_SOURCE must be one of {', '.join(WALLET_SOURCES)}")

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            reports_dir=Path(os.getenv("REPORTS_DIR", "reports")),
            wallet_source=os.getenv("WALLET_SOURCE", "").strip().lower() or "manual",
            wallet_file=Path(os.getenv("WALLET_FILE") or "wallet.json"),
            cache_dir=Path(os.getenv("CACHE_DIR") or _default_cache_dir()),
            http_timeout_seconds=float(os.getenv("HTTP_TIMEOUT_SECONDS", "15")),
            sec_user_agent=os.getenv("SEC_USER_AGENT", "").strip() or "crewai-invest-reporter/0.1",
            llm=LlmSettings(
                model=os.getenv("MODEL", "").strip(),
                bifrost_base_url=os.getenv("BIFROST_BASE_URL", "").strip().rstrip("/"),
                bifrost_api_key=os.getenv("BIFROST_API_KEY", "").strip(),
            ),
            pluggy=PluggySettings(
                client_id=os.getenv("PLUGGY_CLIENT_ID", "").strip(),
                client_secret=os.getenv("PLUGGY_CLIENT_SECRET", "").strip(),
                item_ids=_split_csv(os.getenv("PLUGGY_ITEM_IDS")),
                base_url=os.getenv("PLUGGY_BASE_URL", "https://api.pluggy.ai").rstrip("/"),
            ),
        )
