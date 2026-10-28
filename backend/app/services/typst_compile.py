"""Sandboxed Typst compile and PDF text extraction (ADR-009, ADR-023).

The project root is a throwaway directory. Model JSON, when there is any, is a
file inside that root and is passed as a relative ``--input``. The process
environment does not inherit Sparky secrets. Package lookup stays inside the
root so a template cannot import a cached package from the host.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_TIMEOUT_SECONDS = 30
_MAX_PDF_BYTES = 20_000_000
MAX_RAW_MARKUP_CHARS = 200_000
_PACKAGE_IMPORT = re.compile(r"(#import\b|#include\b|@preview/)", re.IGNORECASE)
_FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/truetype/roboto"),
    Path("/usr/share/fonts/truetype/roboto/unhinted/RobotoTTF"),
)


class CompileError(RuntimeError):
    """Typst or pdftotext could not produce a text-backed PDF."""


@dataclass(frozen=True)
class CompiledPdf:
    pdf: bytes
    source: str
    text: str


def compile_template(
    template_name: str,
    *,
    model_json: str,
    language: str,
    settings: Settings,
) -> CompiledPdf:
    path = TEMPLATES_DIR / template_name
    if not path.is_file():
        raise CompileError(f"unknown template {template_name}")
    source = path.read_text(encoding="utf-8")
    return _compile(source, model_json=model_json, language=language, settings=settings)


def compile_raw_markup(markup: str, *, language: str, settings: Settings) -> CompiledPdf:
    if len(markup) > MAX_RAW_MARKUP_CHARS:
        raise CompileError("markup is too large")
    if not markup.strip():
        raise CompileError("markup is empty")
    return _compile(markup, model_json=None, language=language, settings=settings)


def extract_pdf_text(pdf_path: Path, settings: Settings) -> str:
    if not pdf_path.is_file():
        raise CompileError("PDF is missing")
    return normalize_pdf_text(_pdftotext(pdf_path, settings))


def normalize_pdf_text(text: str) -> str:
    return " ".join(text.split())


def _compile(
    source: str,
    *,
    model_json: str | None,
    language: str,
    settings: Settings,
) -> CompiledPdf:
    if language not in {"en", "sk"}:
        raise CompileError("language must be en or sk")
    if _PACKAGE_IMPORT.search(source):
        raise CompileError("markup cannot import packages or include files")
    with tempfile.TemporaryDirectory(prefix="sparky-typst-") as tmp:
        root = Path(tmp)
        (root / "main.typ").write_text(source, encoding="utf-8")
        if model_json is not None:
            (root / "model.json").write_text(model_json, encoding="utf-8")
        (root / "packages").mkdir()
        (root / "package-cache").mkdir()
        _run_typst(root, model_json is not None, language, settings)
        pdf_path = root / "out.pdf"
        if not pdf_path.is_file():
            raise CompileError("typst did not write a PDF")
        pdf = pdf_path.read_bytes()
        if len(pdf) > _MAX_PDF_BYTES:
            raise CompileError("compiled PDF is too large")
        if not pdf.startswith(b"%PDF"):
            raise CompileError("compiler did not produce a PDF")
        text = normalize_pdf_text(_pdftotext(pdf_path, settings))
    return CompiledPdf(pdf=pdf, source=source, text=text)


def _run_typst(root: Path, with_data: bool, language: str, settings: Settings) -> None:
    command = [
        settings.typst_bin,
        "compile",
        "--root",
        str(root),
        "--ignore-system-fonts",
        "--package-path",
        str(root / "packages"),
        "--package-cache-path",
        str(root / "package-cache"),
    ]
    fonts = _font_dirs(settings)
    if fonts:
        command.extend(["--font-path", os.pathsep.join(str(path) for path in fonts)])
    if with_data:
        command.extend(["--input", "data=model.json"])
    command.extend(["--input", f"lang={language}", "main.typ", "out.pdf"])
    completed = _run(command, root)
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 2000:
            detail = detail[:2000]
        raise CompileError(detail or f"typst exited {completed.returncode}")


def _pdftotext(pdf_path: Path, settings: Settings) -> str:
    completed = _run(
        [settings.pdftotext_bin, "-enc", "UTF-8", "-layout", str(pdf_path), "-"],
        pdf_path.parent,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CompileError(detail or f"pdftotext exited {completed.returncode}")
    return completed.stdout.decode("utf-8", errors="replace")


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(cwd),
        "TMPDIR": str(cwd),
    }
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            check=False,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise CompileError(f"{Path(command[0]).name} timed out") from exc
    except FileNotFoundError as exc:
        raise CompileError(f"{Path(command[0]).name} is not installed") from exc


def _font_dirs(settings: Settings) -> list[Path]:
    candidates: list[Path] = []
    if settings.font_dir:
        candidates.append(Path(settings.font_dir))
    candidates.extend(_FONT_CANDIDATES)
    found: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen or not path.is_dir():
            continue
        seen.add(key)
        found.append(path)
    return found
