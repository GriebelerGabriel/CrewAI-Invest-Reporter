"""Disk cache for slowly changing public datasets (CVM open data)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import requests

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.providers.http import BROWSER_HEADERS, build_session

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 24 * 60 * 60


class DiskCachedFetcher:
    """GET a URL at most once per TTL; serve a stale copy if the origin is unreachable."""

    def __init__(
        self,
        cache_dir: Path,
        session: requests.Session | None = None,
        timeout: float = 60.0,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cache_dir = cache_dir
        self._session = session or build_session(BROWSER_HEADERS)
        self._timeout = timeout
        self._ttl = ttl_seconds
        self._clock = clock

    def get(self, url: str, cache_key: str) -> bytes:
        cached = self._cache_dir / cache_key
        if cached.exists() and self._clock() - cached.stat().st_mtime < self._ttl:
            return cached.read_bytes()
        try:
            response = self._session.get(url, timeout=self._timeout)
        except requests.RequestException as exc:
            return self._stale_or_raise(cached, f"Download of {url} failed: {exc}")
        if response.status_code != 200:
            return self._stale_or_raise(cached, f"{url} returned HTTP {response.status_code}")
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(response.content)
        return response.content

    @staticmethod
    def _stale_or_raise(cached: Path, message: str) -> bytes:
        """Prefer slightly outdated official data over no data at all."""
        if cached.exists():
            logger.warning("%s; using stale cache %s", message, cached)
            return cached.read_bytes()
        raise ProviderError(message)
