from __future__ import annotations

import pytest

from app.services.parser import (
    chunk_sections,
    detect_language,
    extract_sections,
    sections_from_html,
    sections_from_markdown,
)


def test_chunks_keep_headings_and_stay_near_the_token_target() -> None:
    words = " ".join(f"w{index}" for index in range(1000))
    markdown = f"# Experience\n\n{words}\n\n# Education\n\nShort degree line.\n"
    sections = sections_from_markdown(markdown)
    chunks = chunk_sections(sections, target_tokens=400)
    experience = [chunk for chunk in chunks if chunk.section_header == "Experience"]
    education = [chunk for chunk in chunks if chunk.section_header == "Education"]
    assert [chunk.token_count for chunk in experience] == [400, 400, 200]
    assert len(education) == 1
    assert education[0].content == "Short degree line."
    assert len({chunk.content_hash for chunk in chunks}) == len(chunks)


def test_html_headings_become_sections() -> None:
    sections = sections_from_html(
        "<h1>Experience</h1><p>Ran Kubernetes.</p><h2>Skills</h2><p>Python</p>"
    )
    assert [section.header for section in sections] == ["Experience", "Skills"]
    assert "Kubernetes" in sections[0].text
    assert "Python" in sections[1].text


def test_pdf_without_docling_names_the_parse_extra(tmp_path) -> None:
    path = tmp_path / "cv.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(RuntimeError, match="parse extra"):
        extract_sections(path)


def test_language_detection_separates_slovak_and_english() -> None:
    assert detect_language("Spoločnosť hľadá človeka so skúsenosťami.") == "sk"
    assert detect_language("Built platforms with Python for the team.") == "en"
    assert detect_language("") == "unknown"
