from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.services.scraper import fetch_posting, needs_fallback, parse_job_html

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
    assert needs_fallback(200, html, job) is False


def test_challenge_and_missing_json_ld_need_fallback() -> None:
    challenge = (FIXTURES / "profesia_challenge.html").read_text(encoding="utf-8")
    _markdown, job, _title = parse_job_html(challenge)
    assert job is None
    assert needs_fallback(200, challenge, job) is True
    assert needs_fallback(403, "<html>ok</html>", {"title": "x"}) is True
    assert needs_fallback(503, "<html>ok</html>", {"title": "x"}) is True
    assert needs_fallback(200, "<html><p>no schema</p></html>", None) is True


@pytest.mark.asyncio
async def test_fetch_uses_cache_and_skips_fallback_on_json_ld(tmp_path: Path) -> None:
    html = (FIXTURES / "profesia_posting.html").read_text(encoding="utf-8")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
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
    assert Path(fetched.cache_path).read_text(encoding="utf-8") == html


@pytest.mark.asyncio
async def test_fetch_falls_back_when_json_ld_is_missing(tmp_path: Path) -> None:
    async def fallback(url: str) -> str:
        return "Rendered posting body"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><p>empty shell</p></html>")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetched = await fetch_posting(
            "https://www.profesia.sk/praca/?id=5555555",
            cache_dir=tmp_path,
            client=client,
            fallback=fallback,
        )
    assert fetched.used_fallback is True
    assert fetched.markdown == "Rendered posting body"
    assert (tmp_path / "5555555.html").is_file()
