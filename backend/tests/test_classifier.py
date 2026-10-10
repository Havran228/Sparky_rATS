from __future__ import annotations

import pytest

from app.services.scraper import InputKind, classify, profesia_url_for


@pytest.mark.parametrize(
    ("value", "kind", "profesia_id"),
    [
        ("1234567", InputKind.PROFESIA_ID, "1234567"),
        ("id:7654321", InputKind.PROFESIA_ID, "7654321"),
        ("ponuka 1111111", InputKind.PROFESIA_ID, "1111111"),
        ("profesia=2222222", InputKind.PROFESIA_ID, "2222222"),
        (
            "https://www.profesia.sk/praca/northwind/O3333333/",
            InputKind.PROFESIA_URL,
            "3333333",
        ),
        (
            "https://profesia.sk/praca/search?id=4444444",
            InputKind.PROFESIA_URL,
            "4444444",
        ),
        ("https://example.com/jobs/1", InputKind.OTHER_URL, None),
        ("Python developer in Bratislava, od 3000 EUR", InputKind.PASTED_TEXT, None),
    ],
)
def test_classifier_table(value: str, kind: InputKind, profesia_id: str | None) -> None:
    classified = classify(value)
    assert classified.kind is kind
    assert classified.profesia_id == profesia_id


def test_file_classification_uses_mime() -> None:
    classified = classify("scan", mime_type="image/jpeg", filename="poster.jpg")
    assert classified.kind is InputKind.FILE
    assert classified.mime_type == "image/jpeg"
    pdf = classify("cv.pdf")
    assert pdf.kind is InputKind.FILE
    assert pdf.mime_type == "application/pdf"
    assert profesia_url_for("1234567") == "https://www.profesia.sk/praca/?id=1234567"
