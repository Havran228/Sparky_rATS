"""Heading-aware parsing and chunking (ADR-006, ADR-008, ADR-022).

Plain text, Markdown, and HTML parse here. PDF and DOCX go through Docling
when the parse extra is installed. Image files return no text so the caller
can send them to the vision role.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from app.docling_convert import docling_markdown

_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SK_MARKS = set("áäčďéíĺľňóôŕšťúýž")
_EN_WORDS = frozenset({"the", "and", "with", "experience", "for", "from", "this"})
_SK_WORDS = frozenset(
    {"som", "praxe", "skusenosti", "zodpovedny", "pre", "pracu", "spolocnost", "pozicia"}
)
_TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".html", ".htm"}
_DOCLING_SUFFIXES = {".pdf", ".docx", ".doc"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".tif", ".tiff", ".gif", ".bmp"}


@dataclass(frozen=True)
class ParsedSection:
    header: str
    text: str


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    section_header: str
    content: str
    content_hash: str
    token_count: int


def extract_sections(path: Path, mime_type: str | None = None) -> list[ParsedSection]:
    suffix = path.suffix.lower()
    kind = (mime_type or "").split(";", 1)[0].strip().lower()
    if kind.startswith("image/") or suffix in _IMAGE_SUFFIXES:
        return []
    if suffix in _DOCLING_SUFFIXES or kind in {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }:
        markdown = docling_markdown(path)
        if markdown is None:
            raise RuntimeError(
                "PDF and DOCX need the optional parse extra. "
                "Install it with: pip install 'sparky-rats[parse]'"
            )
        return sections_from_markdown(markdown)
    raw = path.read_text(encoding="utf-8", errors="replace")
    if suffix in {".html", ".htm"} or kind == "text/html":
        return sections_from_html(raw)
    if suffix in {".md", ".markdown"} or kind == "text/markdown":
        return sections_from_markdown(raw)
    if suffix in _TEXT_SUFFIXES or kind.startswith("text/") or not suffix:
        return sections_from_markdown(raw) if _HEADING.search(raw) else _plain(raw)
    raise RuntimeError(f"unsupported file type for {path.name}")


def sections_from_markdown(markdown: str) -> list[ParsedSection]:
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading:
            sections.append((heading.group(2).strip(), []))
            continue
        sections[-1][1].append(line)
    return _pack(sections)


def sections_from_html(html: str) -> list[ParsedSection]:
    parser = _HTMLSections()
    parser.feed(html)
    parser.close()
    return _pack(parser.sections)


def chunk_sections(sections: list[ParsedSection], *, target_tokens: int = 400) -> list[TextChunk]:
    if target_tokens < 1:
        raise ValueError("target_tokens must be positive")
    chunks: list[TextChunk] = []
    for section in sections:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", section.text) if part.strip()]
        if not paragraphs and section.text.strip():
            paragraphs = [section.text.strip()]
        bucket: list[str] = []
        count = 0
        for paragraph in paragraphs:
            words = paragraph.split()
            if not words:
                continue
            if count and count + len(words) > target_tokens:
                _emit(chunks, section.header, bucket)
                bucket = []
                count = 0
            if len(words) <= target_tokens:
                bucket.append(paragraph)
                count += len(words)
                continue
            if bucket:
                _emit(chunks, section.header, bucket)
                bucket = []
                count = 0
            start = 0
            while start < len(words):
                piece = words[start : start + target_tokens]
                _emit(chunks, section.header, [" ".join(piece)])
                start += target_tokens
        if bucket:
            _emit(chunks, section.header, bucket)
    for index, chunk in enumerate(chunks):
        chunks[index] = TextChunk(
            chunk_index=index,
            section_header=chunk.section_header,
            content=chunk.content,
            content_hash=chunk.content_hash,
            token_count=chunk.token_count,
        )
    return chunks


def detect_language(text: str) -> str:
    letters = [char for char in text.casefold() if char.isalpha()]
    if not letters:
        return "unknown"
    marks = sum(1 for char in text.casefold() if char in _SK_MARKS)
    words = set(fold_diacritics(text).split())
    if marks >= 2 or (marks / len(letters)) >= 0.015:
        return "sk"
    sk_hits = len(words & _SK_WORDS)
    en_hits = len(words & _EN_WORDS)
    if sk_hits > en_hits and sk_hits > 0:
        return "sk"
    if en_hits > 0:
        return "en"
    return "unknown"


def fold_diacritics(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def joined_text(sections: list[ParsedSection]) -> str:
    parts: list[str] = []
    for section in sections:
        if section.header:
            parts.append(f"# {section.header}\n{section.text}".strip())
        else:
            parts.append(section.text.strip())
    return "\n\n".join(part for part in parts if part).strip()


def _plain(text: str) -> list[ParsedSection]:
    body = text.strip()
    if not body:
        return []
    return [ParsedSection(header="", text=body)]


def _pack(sections: list[tuple[str, list[str]]]) -> list[ParsedSection]:
    packed: list[ParsedSection] = []
    for header, lines in sections:
        body = "\n".join(lines).strip()
        if not body and not header:
            continue
        if not body:
            continue
        packed.append(ParsedSection(header=header.strip(), text=body))
    return packed


def _emit(chunks: list[TextChunk], header: str, paragraphs: list[str]) -> None:
    content = "\n\n".join(paragraphs).strip()
    if not content:
        return
    chunks.append(
        TextChunk(
            chunk_index=len(chunks),
            section_header=header,
            content=content,
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            token_count=len(content.split()),
        )
    )


class _HTMLSections(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str, list[str]]] = [("", [])]
        self._skip = 0
        self._heading: str | None = None
        self._heading_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in {"h1", "h2", "h3", "h4"}:
            self._heading = tag
            self._heading_parts = []
            return
        if tag in {"p", "li", "br", "div", "tr"} and self._heading is None:
            self.sections[-1][1].append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if tag == self._heading:
            header = " ".join("".join(self._heading_parts).split())
            self.sections.append((header, []))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._heading is not None:
            self._heading_parts.append(data)
            return
        self.sections[-1][1].append(data)
