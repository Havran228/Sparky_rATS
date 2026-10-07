"""UTC timestamps stored as ISO-8601 text (ADR-003)."""

from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
