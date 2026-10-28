"""Optional Docling conversion for PDF and DOCX (ADR-006, ADR-022).

The default install does not pull Torch. When the parse extra is absent this
returns None and the caller reports that PDF/DOCX parsing is unavailable.
"""

from __future__ import annotations

import importlib
from pathlib import Path


def docling_markdown(path: Path) -> str | None:
    if importlib.util.find_spec("docling") is None:
        return None
    module = importlib.import_module("docling.document_converter")
    converter = module.DocumentConverter()
    result = converter.convert(str(path))
    text = result.document.export_to_markdown()
    if not isinstance(text, str):
        raise RuntimeError("Docling did not return markdown")
    return text
