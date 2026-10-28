"""Profesia fetch headers and HTML parsing when JSON-LD is absent."""

from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup
from bs4.element import Tag

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "sk,cs,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

_TITLE_SELECTORS = ("h1", ".title", "h1.title")
_COMPANY_SELECTORS = (
    "[itemprop=hiringOrganization]",
    ".employer",
    ".employer-name",
    "a.employer",
    ".company",
    ".company-name",
)
_SALARY_SELECTORS = (
    ".salary",
    ".compensation",
    "[class*=salary]",
    "[class*=wage]",
    "[class*=plat]",
)
_BODY_SELECTORS = (
    "div.details",
    "div.job-ad",
    ".job-description",
    "#job-description",
    "[itemprop=description]",
    "article",
    "main",
)
_MIN_BODY_CHARS = 100
_SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "form", "svg"}


@dataclass(frozen=True)
class HtmlPosting:
    title: str | None
    company: str | None
    salary: str | None
    markdown: str

    def as_job_posting(self) -> dict[str, object] | None:
        if not self.title and not self.company and not self.salary:
            return None
        posting: dict[str, object] = {"@type": "JobPosting"}
        if self.title:
            posting["title"] = self.title
        if self.company:
            posting["hiringOrganization"] = {"@type": "Organization", "name": self.company}
        return posting


def parse_profesia_html(html: str) -> HtmlPosting:
    soup = BeautifulSoup(html, "lxml")
    for tag_name in ("script", "style", "noscript"):
        for node in soup.find_all(tag_name):
            node.decompose()
    title = _title(soup)
    company = _company(soup)
    salary = _salary(soup)
    body = _job_body(soup)
    description = _markdown(body).strip()
    lines: list[str] = []
    if title:
        lines.append(f"# {title}")
    if company:
        lines.append(f"Company: {company}")
    if salary:
        lines.append(f"Salary: {salary}")
    if description and description not in "\n".join(lines):
        lines.append(description)
    return HtmlPosting(
        title=title,
        company=company,
        salary=salary,
        markdown="\n\n".join(line for line in lines if line.strip()),
    )


def _title(soup: BeautifulSoup) -> str | None:
    node = _first(soup, _TITLE_SELECTORS)
    if node is not None:
        text = _clean(node.get_text(" ", strip=True))
        if text:
            return text.split("|", 1)[0].strip()
    meta = _meta(soup, "og:title") or _meta(soup, "title")
    if meta:
        return meta.split("|", 1)[0].strip()
    if soup.title and soup.title.string:
        return _clean(soup.title.string).split("|", 1)[0].strip() or None
    return None


def _company(soup: BeautifulSoup) -> str | None:
    node = _first(soup, _COMPANY_SELECTORS)
    if node is not None:
        text = _clean(node.get_text(" ", strip=True))
        if text:
            return text
    return _meta(soup, "og:site_name")


def _salary(soup: BeautifulSoup) -> str | None:
    node = _first(soup, _SALARY_SELECTORS)
    if node is not None:
        text = _clean(node.get_text(" ", strip=True))
        if text:
            return text
    for text in soup.stripped_strings:
        lowered = text.lower()
        if "€" in text or "eur" in lowered or "brutto" in lowered or "netto" in lowered:
            cleaned = _clean(text)
            if any(character.isdigit() for character in cleaned):
                return cleaned
    return None


def _job_body(soup: BeautifulSoup) -> Tag:
    """Prefer the real ad container over a short JSON-LD blurb or the whole page."""
    best: Tag | None = None
    best_length = 0
    for selector in _BODY_SELECTORS:
        found = soup.select_one(selector)
        if not isinstance(found, Tag):
            continue
        length = len(_clean(found.get_text(" ", strip=True)))
        if length >= _MIN_BODY_CHARS:
            return found
        if length > best_length:
            best = found
            best_length = length
    if best is not None:
        return best
    return soup.body or soup


def _first(soup: BeautifulSoup, selectors: tuple[str, ...]) -> Tag | None:
    for selector in selectors:
        found = soup.select_one(selector)
        if isinstance(found, Tag):
            return found
    return None


def _meta(soup: BeautifulSoup, key: str) -> str | None:
    node = soup.select_one(f'meta[property="{key}"], meta[name="{key}"]')
    if not isinstance(node, Tag):
        return None
    content = node.get("content")
    if isinstance(content, str) and content.strip():
        return _clean(content)
    return None


def _markdown(node: Tag) -> str:
    parts: list[str] = []
    for child in list(node.children):
        if not isinstance(child, Tag):
            text = _clean(str(child))
            if text:
                parts.append(text)
            continue
        if child.name in _SKIP_TAGS:
            continue
        if child.name in {"h1", "h2", "h3"}:
            level = int(child.name[1])
            text = _clean(child.get_text(" ", strip=True))
            if text:
                parts.append(f"{'#' * level} {text}")
            continue
        if child.name == "li":
            text = _clean(child.get_text(" ", strip=True))
            if text:
                parts.append(f"- {text}")
            continue
        if child.name in {"ul", "ol"}:
            items = _markdown(child).strip()
            if items:
                parts.append(items)
            continue
        nested = _markdown(child).strip()
        if nested:
            parts.append(nested)
    return "\n\n".join(part for part in parts if part.strip())


def _clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())
