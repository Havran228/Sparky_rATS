"""Versioned prompt assets (ADR-018)."""

from __future__ import annotations

from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> tuple[str, str]:
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8").strip()
    return text, name
