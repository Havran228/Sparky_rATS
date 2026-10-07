"""SQLite in WAL mode, foreign keys, and numbered SQL migrations (ADR-003)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.clock import utc_now

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> int:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor.rowcount

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params))

    def query_one(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        """Exclusive transaction. Do not call execute/query inside the block."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def migrate(self) -> list[int]:
        self.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        applied = {int(row["version"]) for row in self.query("SELECT version FROM schema_version")}
        done: list[int] = []
        for version, name, path in _migration_files():
            if version in applied:
                continue
            script = path.read_text(encoding="utf-8")
            with self._lock:
                self._conn.executescript(script)
                self._conn.execute(
                    "INSERT INTO schema_version (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, utc_now()),
                )
                self._conn.commit()
            done.append(version)
        return done


def _migration_files() -> list[tuple[int, str, Path]]:
    found: list[tuple[int, str, Path]] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        prefix, _, rest = path.name.partition("_")
        if not prefix.isdigit() or not rest.endswith(".sql"):
            raise RuntimeError(f"migration file name must be NNNN_name.sql, got {path.name}")
        name = rest[: -len(".sql")]
        found.append((int(prefix), name, path))
    return found
