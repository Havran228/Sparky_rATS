"""Lazy Crawl4AI fallback. Chromium is installed on first use (ADR-006)."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

_chromium_ready = False


class FallbackUnavailable(RuntimeError):
    """The headless fallback could not run."""


async def markdown(url: str) -> str:
    try:
        from crawl4ai import AsyncWebCrawler  # type: ignore[import-not-found]
    except ImportError as exc:
        raise FallbackUnavailable(
            "Crawl4AI is not installed. JSON-LD was missing and the headless fallback "
            "is unavailable."
        ) from exc
    _ensure_chromium()
    crawler: Any = AsyncWebCrawler()
    async with crawler:
        result: Any = await crawler.arun(url=url)
    text = getattr(result, "markdown", None)
    if isinstance(text, str) and text.strip():
        return text
    html = getattr(result, "cleaned_html", None) or getattr(result, "html", None)
    if isinstance(html, str) and html.strip():
        return html
    raise FallbackUnavailable("Crawl4AI returned no page text")


def _ensure_chromium() -> None:
    global _chromium_ready
    if _chromium_ready:
        return
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
        capture_output=True,
    )
    _chromium_ready = True
