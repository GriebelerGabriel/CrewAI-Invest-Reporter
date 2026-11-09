"""Download a remote document and extract its text (PDF, HTML, XML or plain text).

URLs come from LLM agents, so every hop (including redirects) is checked to only reach public
http(s) hosts, and downloads are size-capped.
"""

from __future__ import annotations

import io
import ipaddress
import socket
from collections import OrderedDict
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.domain.models import Document
from crewai_invest_reporter.domain.ports import DocumentReader
from crewai_invest_reporter.providers.http import BROWSER_HEADERS, build_session

MAX_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 60
MAX_REDIRECTS = 5

Resolver = Callable[[str], list[str]]


def _resolve_host(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


class UnsafeUrlError(ProviderError):
    """Raised for URLs pointing to non-public hosts or unsupported schemes."""


class HttpDocumentReader:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: float = 60.0,
        max_bytes: int = MAX_BYTES,
        resolve_host: Resolver = _resolve_host,
        domain_headers: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self._session = session or build_session(BROWSER_HEADERS)
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._resolve_host = resolve_host
        # e.g. {"sec.gov": {"User-Agent": ...}}: some sources require their own headers.
        self._domain_headers = domain_headers or {}

    def read(self, url: str) -> Document:
        content, content_type = self._download(url)
        if content.lstrip()[:5] == b"%PDF-" or "pdf" in content_type:
            text, pages = _pdf_text(content)
            return Document(url=url, content_type="application/pdf", text=text, pages=pages)
        if "xml" in content_type or content.lstrip()[:5] == b"<?xml":
            soup = BeautifulSoup(content, "xml")
            return Document(url=url, content_type="text/xml", text=soup.get_text("\n", strip=True))
        if "html" in content_type or b"<html" in content[:2048].lower():
            return Document(url=url, content_type="text/html", text=_html_text(content))
        if content_type.startswith("text/") or not content_type:
            return Document(url=url, content_type="text/plain", text=_decode(content))
        raise ProviderError(f"Unsupported document type {content_type!r} at {url}")

    def _download(self, url: str) -> tuple[bytes, str]:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            self._ensure_public(current)
            try:
                response = self._session.get(
                    current,
                    headers=self._headers_for(current),
                    timeout=self._timeout,
                    stream=True,
                    allow_redirects=False,
                )
            except requests.RequestException as exc:
                raise ProviderError(f"Download failed for {current}: {exc}") from exc
            with response:
                if response.is_redirect:
                    current = urljoin(current, response.headers.get("Location", ""))
                    continue
                if response.status_code != 200:
                    raise ProviderError(f"{current} returned HTTP {response.status_code}")
                content_type = response.headers.get("Content-Type", "").lower()
                return self._read_capped(response), content_type
        raise ProviderError(f"Too many redirects for {url}")

    def _headers_for(self, url: str) -> dict[str, str]:
        host = urlparse(url).hostname or ""
        for domain, headers in self._domain_headers.items():
            if host == domain or host.endswith(f".{domain}"):
                return headers
        return {}

    def _read_capped(self, response: requests.Response) -> bytes:
        buffer = io.BytesIO()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            buffer.write(chunk)
            if buffer.tell() > self._max_bytes:
                raise ProviderError(f"Document larger than {self._max_bytes // (1024 * 1024)} MB")
        return buffer.getvalue()

    def _ensure_public(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnsafeUrlError(f"Only public http(s) URLs can be read: {url!r}")
        try:
            addresses = self._resolve_host(parsed.hostname)
        except OSError as exc:
            raise ProviderError(f"Cannot resolve host {parsed.hostname}: {exc}") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address.split("%")[0])
            if not ip.is_global:
                raise UnsafeUrlError(f"Refusing to access non-public address {ip} ({url})")


def _pdf_text(content: bytes) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = reader.pages
        texts = [
            f"[página {number}]\n{page.extract_text() or ''}"
            for number, page in enumerate(pages[:MAX_PDF_PAGES], start=1)
        ]
    except (PdfReadError, ValueError, KeyError) as exc:
        raise ProviderError(f"Could not read PDF: {exc}") from exc
    return "\n".join(texts), len(pages)


def _html_text(content: bytes) -> str:
    soup = BeautifulSoup(content, "lxml")
    for tag in soup(["script", "style", "noscript", "nav", "footer", "header", "form"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _decode(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("latin-1")


class CachedDocumentReader:
    """Keep recently read documents in memory so chunked reads don't re-download them."""

    def __init__(self, inner: DocumentReader, max_entries: int = 16) -> None:
        self._inner = inner
        self._max_entries = max_entries
        self._cache: OrderedDict[str, Document] = OrderedDict()

    def read(self, url: str) -> Document:
        if url in self._cache:
            self._cache.move_to_end(url)
            return self._cache[url]
        document = self._inner.read(url)
        self._cache[url] = document
        if len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)
        return document
