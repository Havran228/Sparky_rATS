"""Profesia input classification and JSON-LD fetch (ADR-006).

Tests call the parser on cached HTML. Nothing in the test suite contacts the live site.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.crawl_fallback import FallbackUnavailable
from app.crawl_fallback import markdown as crawl_markdown
from app.ingest.profesia import BROWSER_HEADERS, parse_profesia_html

_ID_RE = re.compile(
    r"^(?:(?:profesia|id|ponuka)[:\s=/-]*)?(\d{7})$",
    re.IGNORECASE,
)
_URL_ID_RE = re.compile(
    r"/O(\d{7})(?!\d)|[?&]id=(\d{7})(?!\d)",
    re.IGNORECASE,
)
_FILE_RE = re.compile(r"\.(pdf|docx|doc|png|jpe?g|webp|heic|tiff?)$", re.IGNORECASE)
_CHALLENGE_RE = re.compile(
    r"just a moment|cf-browser-verification|cf-mitigated|g-recaptcha|hcaptcha",
    re.IGNORECASE,
)
_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

Fallback = Callable[[str], Awaitable[str]]


class InputKind(StrEnum):
    PROFESIA_ID = "profesia_id"
    PROFESIA_URL = "profesia_url"
    OTHER_URL = "other_url"
    FILE = "file"
    PASTED_TEXT = "pasted_text"


@dataclass(frozen=True)
class ClassifiedInput:
    kind: InputKind
    profesia_id: str | None = None
    url: str | None = None
    mime_type: str | None = None
    filename: str | None = None


@dataclass(frozen=True)
class FetchedPosting:
    url: str
    status_code: int
    html: str
    markdown: str
    job_posting: dict[str, object] | None
    used_fallback: bool
    cache_path: str


class HostRateLimiter:
    def __init__(self, min_interval_seconds: float = 1.5) -> None:
        self._interval = min_interval_seconds
        self._next: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def wait(self, host: str) -> None:
        async with self._lock:
            now = time.monotonic()
            ready = self._next.get(host, 0.0)
            delay = max(0.0, ready - now)
            self._next[host] = max(now, ready) + self._interval
        if delay > 0:
            await asyncio.sleep(delay)


def classify(
    value: str,
    *,
    mime_type: str | None = None,
    filename: str | None = None,
) -> ClassifiedInput:
    text = value.strip()
    named = filename or text
    if mime_type or _FILE_RE.search(named):
        suffix = Path(named).suffix.lower()
        return ClassifiedInput(
            kind=InputKind.FILE,
            mime_type=mime_type or _MIME_BY_SUFFIX.get(suffix),
            filename=Path(named).name,
        )
    if text.lower().startswith(("http://", "https://")):
        profesia_id = _url_id(text)
        kind = InputKind.PROFESIA_URL if profesia_id else InputKind.OTHER_URL
        return ClassifiedInput(kind=kind, profesia_id=profesia_id, url=text)
    id_match = _ID_RE.match(text)
    if id_match:
        return ClassifiedInput(kind=InputKind.PROFESIA_ID, profesia_id=id_match.group(1))
    return ClassifiedInput(kind=InputKind.PASTED_TEXT)


def profesia_url_for(profesia_id: str) -> str:
    return f"https://www.profesia.sk/praca/?id={profesia_id}"


def needs_headless_fallback(status_code: int, html: str) -> bool:
    """Crawl4AI is only for a blocked response. Missing JSON-LD stays on the HTML parser."""
    if status_code in {403, 503}:
        return True
    return _CHALLENGE_RE.search(html) is not None


def parse_job_html(html: str) -> tuple[str, dict[str, object] | None, str]:
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    markdown = parser.markdown()
    title = parser.title.strip()
    if title and title not in markdown:
        markdown = f"{title}\n\n{markdown}".strip()
    return markdown, _job_posting(parser.json_ld), title


async def fetch_posting(
    url: str,
    *,
    cache_dir: Path,
    client: httpx.AsyncClient | None = None,
    limiter: HostRateLimiter | None = None,
    fallback: Fallback | None = None,
) -> FetchedPosting:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=BROWSER_HEADERS)
    try:
        host = urlparse(url).netloc or "unknown"
        if limiter is not None:
            await limiter.wait(host)
        response = await http.get(url, headers=BROWSER_HEADERS)
        html = response.text
        cache_path = _write_cache(cache_dir, url, html)
        markdown, job, _title = parse_job_html(html)
        status = response.status_code
        html_posting = parse_profesia_html(html)
        if job is None:
            job = html_posting.as_job_posting()
            if html_posting.markdown:
                markdown = html_posting.markdown
        if not needs_headless_fallback(status, html):
            return FetchedPosting(
                url=url,
                status_code=status,
                html=html,
                markdown=markdown,
                job_posting=job,
                used_fallback=False,
                cache_path=str(cache_path),
            )
        rendered = await _optional_headless(url, fallback)
        return FetchedPosting(
            url=url,
            status_code=status,
            html=html,
            markdown=rendered or markdown,
            job_posting=job,
            used_fallback=rendered is not None,
            cache_path=str(cache_path),
        )
    finally:
        if owns_client:
            await http.aclose()


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.json_ld: list[str] = []
        self.title = ""
        self._capture_json = False
        self._json_buf: list[str] = []
        self._skip = 0
        self._in_title = False
        self._text: list[str] = []
        self._blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "script" and "ld+json" in attributes.get("type", ""):
            self._capture_json = True
            self._json_buf = []
            return
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in {"p", "h1", "h2", "h3", "li", "br", "tr"}:
            self._flush_text()

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture_json:
            self.json_ld.append("".join(self._json_buf).strip())
            self._capture_json = False
            return
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in {"p", "h1", "h2", "h3", "li", "tr"}:
            self._flush_text()

    def handle_data(self, data: str) -> None:
        if self._capture_json:
            self._json_buf.append(data)
            return
        if self._skip:
            return
        stripped = data.strip()
        if not stripped:
            return
        if self._in_title:
            self.title = f"{self.title} {stripped}".strip()
        self._text.append(stripped)

    def markdown(self) -> str:
        self._flush_text()
        return "\n\n".join(block for block in self._blocks if block)

    def _flush_text(self) -> None:
        if not self._text:
            return
        self._blocks.append(" ".join(self._text))
        self._text = []


async def _optional_headless(url: str, fallback: Fallback | None) -> str | None:
    fallback_fn = crawl_markdown if fallback is None else fallback
    try:
        rendered = await fallback_fn(url)
    except FallbackUnavailable:
        return None
    if isinstance(rendered, str) and rendered.strip():
        return rendered.strip()
    return None


def _url_id(url: str) -> str | None:
    match = _URL_ID_RE.search(url)
    if match is None:
        return None
    return match.group(1) or match.group(2)


def _job_posting(blocks: list[str]) -> dict[str, object] | None:
    for block in blocks:
        if not block:
            continue
        try:
            loaded: object = json.loads(block)
        except json.JSONDecodeError:
            continue
        found = _find_job(loaded)
        if found is not None:
            return found
    return None


def _find_job(value: object) -> dict[str, object] | None:
    if isinstance(value, list):
        for item in value:
            found = _find_job(item)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict):
        return None
    kind = value.get("@type")
    if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
        return {str(key): item for key, item in value.items()}
    graph = value.get("@graph")
    if graph is not None:
        return _find_job(graph)
    return None


def _write_cache(cache_dir: Path, url: str, html: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    profesia_id = _url_id(url)
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    name = f"{profesia_id}.html" if profesia_id else f"{digest}.html"
    path = cache_dir / name
    path.write_text(html, encoding="utf-8")
    return path
