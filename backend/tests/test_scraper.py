from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.crawl_fallback import FallbackUnavailable
from app.ingest.profesia import BROWSER_HEADERS, parse_profesia_html
from app.services.scraper import (
    description_needs_html_fallback,
    fetch_posting,
    merge_posting_sources,
    needs_headless_fallback,
    parse_job_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_json_ld_fixture_parses_without_network() -> None:
    html = (FIXTURES / "profesia_posting.html").read_text(encoding="utf-8")
    markdown, job, title = parse_job_html(html)
    assert title == "Backend engineer"
    assert job is not None
    assert job["title"] == "Backend engineer"
    org = job["hiringOrganization"]
    assert isinstance(org, dict)
    assert org["name"] == "Northwind s.r.o."
    assert "Python and PostgreSQL" in markdown
    assert "Zodpovednosť za API" in markdown
    assert needs_headless_fallback(200, html) is False


def test_headless_is_only_for_a_blocked_response() -> None:
    challenge = (FIXTURES / "profesia_challenge.html").read_text(encoding="utf-8")
    _markdown, job, _title = parse_job_html(challenge)
    assert job is None
    assert needs_headless_fallback(200, challenge) is True
    assert needs_headless_fallback(403, "<html>ok</html>") is True
    assert needs_headless_fallback(503, "<html>ok</html>") is True
    assert needs_headless_fallback(200, "<html><p>no schema</p></html>") is False


def test_html_fallback_reads_title_company_salary_and_body() -> None:
    html = (FIXTURES / "profesia_html_only.html").read_text(encoding="utf-8")
    parsed = parse_profesia_html(html)
    assert parsed.title == "Backend engineer"
    assert parsed.company == "Northwind s.r.o."
    assert parsed.salary == "2 500 - 3 500 € brutto"
    assert "Python and PostgreSQL." in parsed.markdown
    assert "Zodpovednosť za API" in parsed.markdown
    posting = parsed.as_job_posting()
    assert posting is not None
    assert posting["title"] == "Backend engineer"


@pytest.mark.asyncio
async def test_fetch_uses_cache_and_skips_fallback_on_json_ld(tmp_path: Path) -> None:
    html = (FIXTURES / "profesia_posting.html").read_text(encoding="utf-8")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["user-agent"] == BROWSER_HEADERS["User-Agent"]
        assert request.headers["accept-language"] == "sk,cs,en;q=0.9"
        return httpx.Response(200, text=html)

    async def fallback(url: str) -> str:
        nonlocal calls
        calls += 1
        return "should not run"

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetched = await fetch_posting(
            "https://www.profesia.sk/praca/northwind/O3333333/",
            cache_dir=tmp_path,
            client=client,
            fallback=fallback,
        )
    assert fetched.used_fallback is False
    assert calls == 0
    assert fetched.job_posting is not None
    assert "description" not in fetched.job_posting
    assert "Zodpovednosť za API" in fetched.markdown
    assert Path(fetched.cache_path).read_text(encoding="utf-8") == html


@pytest.mark.asyncio
async def test_missing_json_ld_uses_html_parser_not_crawl4ai(tmp_path: Path) -> None:
    html = (FIXTURES / "profesia_html_only.html").read_text(encoding="utf-8")
    calls = 0

    async def fallback(url: str) -> str:
        nonlocal calls
        calls += 1
        return "should not run"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetched = await fetch_posting(
            "https://www.profesia.sk/praca/?id=5555555",
            cache_dir=tmp_path,
            client=client,
            fallback=fallback,
        )
    assert calls == 0
    assert fetched.used_fallback is False
    assert fetched.job_posting is not None
    assert fetched.job_posting["title"] == "Backend engineer"
    assert "2 500 - 3 500 € brutto" in fetched.markdown
    assert (tmp_path / "5555555.html").is_file()


@pytest.mark.asyncio
async def test_blocked_page_survives_when_crawl4ai_is_missing(tmp_path: Path) -> None:
    html = (FIXTURES / "profesia_html_only.html").read_text(encoding="utf-8")

    async def fallback(url: str) -> str:
        raise FallbackUnavailable("Crawl4AI is not installed")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=html)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetched = await fetch_posting(
            "https://www.profesia.sk/praca/?id=6666666",
            cache_dir=tmp_path,
            client=client,
            fallback=fallback,
        )
    assert fetched.used_fallback is False
    assert fetched.status_code == 403
    assert "Backend engineer" in fetched.markdown


def test_thin_or_boilerplate_json_ld_uses_the_html_ad() -> None:
    assert description_needs_html_fallback(None) is True
    assert description_needs_html_fallback("Online") is True
    assert description_needs_html_fallback("x" * 99) is True
    long_cookie = "Prihláste sa a súhlaste s cookies na Profesia.sk. " + ("text " * 20)
    assert description_needs_html_fallback(long_cookie) is True
    assert description_needs_html_fallback("x" * 100) is False

    html = (FIXTURES / "profesia_thin_jsonld.html").read_text(encoding="utf-8")
    page, job, _title = parse_job_html(html)
    parsed = parse_profesia_html(html)
    markdown, merged = merge_posting_sources(page, job, parsed.markdown)
    assert merged is not None
    assert "description" not in merged
    assert "Telefonovať klientom" in markdown
    assert "Slovenský jazyk na slovom a na písme" in markdown
    assert "Práca z domu" in markdown
    assert "Odporúčané ponuky" not in markdown

    boilerplate = (
        "<html><head><script type='application/ld+json'>"
        '{"@type":"JobPosting","title":"Operátor",'
        '"description":"Prihláste sa. Cookie. Súhlas s GDPR a newsletter."}'
        "</script></head><body>"
        "<div class='job-ad'><p>Obsluha prichádzajúcich hovorov a práca s CRM.</p>"
        "<p>Ponúkame flexi konto a sick days.</p></div></body></html>"
    )
    page, job, _title = parse_job_html(boilerplate)
    parsed = parse_profesia_html(boilerplate)
    markdown, merged = merge_posting_sources(page, job, parsed.markdown)
    assert merged is not None
    assert "description" not in merged
    assert "Obsluha prichádzajúcich hovorov" in markdown
    assert "flexi konto" in markdown
    assert "newsletter" not in markdown.lower()
