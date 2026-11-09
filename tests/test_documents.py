from __future__ import annotations

import io

import pytest
import responses
from pypdf import PdfWriter

from crewai_invest_reporter.domain.errors import ProviderError
from crewai_invest_reporter.providers.documents import (
    CachedDocumentReader,
    HttpDocumentReader,
    UnsafeUrlError,
)
from crewai_invest_reporter.providers.http import build_session
from tests.fakes import FakeDocuments


def _public(_host):
    return ["93.184.216.34"]


def _reader(**kwargs) -> HttpDocumentReader:
    return HttpDocumentReader(session=build_session(retries=0), resolve_host=_public, **kwargs)


def _blank_pdf(pages: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@responses.activate
def test_reads_html_without_scripts_and_navigation():
    responses.get(
        "https://ri.example.com/release",
        body="<html><nav>menu</nav><script>x()</script><p>Lucro de R$ 10 bi</p></html>",
        content_type="text/html",
    )
    document = _reader().read("https://ri.example.com/release")
    assert document.content_type == "text/html"
    assert document.text == "Lucro de R$ 10 bi"


@responses.activate
def test_detects_pdf_by_magic_bytes_even_with_wrong_content_type():
    # CVM's RAD serves PDFs as text/html.
    responses.get("https://rad.example.com/doc", body=_blank_pdf(3), content_type="text/html")
    document = _reader().read("https://rad.example.com/doc")
    assert document.content_type == "application/pdf"
    assert document.pages == 3
    assert "[página 3]" in document.text


@responses.activate
def test_reads_xml_documents():
    responses.get(
        "https://fnet.example.com/doc",
        body='<?xml version="1.0"?><Informe><DY>1,1</DY></Informe>',
        content_type="text/xml",
    )
    assert _reader().read("https://fnet.example.com/doc").text == "1,1"


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "ftp://example.com/x", "http://", "javascript:alert(1)"]
)
def test_rejects_non_http_urls(url):
    with pytest.raises(UnsafeUrlError):
        _reader().read(url)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "::1"])
def test_rejects_private_addresses(address):
    reader = HttpDocumentReader(resolve_host=lambda _host: [address])
    with pytest.raises(UnsafeUrlError):
        reader.read("http://internal.example.com/")


@responses.activate
def test_checks_every_redirect_hop():
    responses.get(
        "https://public.example.com/r", status=302, headers={"Location": "http://internal/admin"}
    )

    def resolve(host):
        return ["10.0.0.1"] if host == "internal" else ["93.184.216.34"]

    reader = HttpDocumentReader(session=build_session(retries=0), resolve_host=resolve)
    with pytest.raises(UnsafeUrlError):
        reader.read("https://public.example.com/r")


@responses.activate
def test_enforces_size_limit_and_http_errors():
    responses.get("https://example.com/big", body=b"x" * 2048, content_type="text/plain")
    responses.get("https://example.com/missing", status=404)
    with pytest.raises(ProviderError, match="larger than"):
        _reader(max_bytes=1024).read("https://example.com/big")
    with pytest.raises(ProviderError, match="HTTP 404"):
        _reader().read("https://example.com/missing")


def test_cached_reader_reuses_documents_and_evicts_oldest():
    inner = FakeDocuments()
    cached = CachedDocumentReader(inner, max_entries=2)
    cached.read("a")
    cached.read("a")
    cached.read("b")
    cached.read("c")  # evicts "a"
    cached.read("a")
    assert inner.calls == ["a", "b", "c", "a"]


@responses.activate
def test_domain_specific_headers_are_sent_only_to_that_domain():
    responses.get(
        "https://www.sec.gov/doc.htm",
        body="<html><p>SEC</p></html>",
        content_type="text/html",
        match=[responses.matchers.header_matcher({"User-Agent": "me contact@x.com"})],
    )
    responses.get("https://other.example.com/doc", body="ok", content_type="text/plain")
    reader = _reader(domain_headers={"sec.gov": {"User-Agent": "me contact@x.com"}})
    assert reader.read("https://www.sec.gov/doc.htm").text == "SEC"
    assert reader.read("https://other.example.com/doc").text == "ok"
    assert responses.calls[1].request.headers["User-Agent"] != "me contact@x.com"
