"""UTC timestamps stored as ISO-8601 text (ADR-003)."""

from datetime import UTC, datetime


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
