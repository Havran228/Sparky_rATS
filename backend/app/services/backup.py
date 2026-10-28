"""SQLite online backup and a logical Qdrant snapshot (ADR-012, ADR-026).

The master key is never copied. A database backup cannot be decrypted without
the separate ``credentials/master.key`` file.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.db.database import Database
from app.vector_store import VectorStore

STAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
REMINDER = (
    "Back up credentials/master.key separately. "
    "The database backup cannot be decrypted without it."
)


class BackupError(RuntimeError):
    """The backup or restore request cannot be completed."""


class BackupNotFound(BackupError):
    """The named backup directory is not in the backups folder."""


@dataclass(frozen=True)
class BackupReport:
    name: str
    relative_path: str
    mode: str
    points: int
    removed: list[str]
    created_at: str

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": self.relative_path,
            "mode": self.mode,
            "sqlite": "sparky_rats.db",
            "qdrant": "points.jsonl",
            "points": self.points,
            "removed": self.removed,
            "created_at": self.created_at,
            "master_key_included": False,
        }


def current_time() -> datetime:
    return datetime.now(UTC)


def create_backup(
    db: Database,
    vectors: VectorStore,
    settings: Settings,
    *,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> BackupReport:
    days = settings.backup_retention_days if retention_days is None else retention_days
    if days < 1:
        raise BackupError("retention_days must be at least 1")
    moment = now or current_time()
    if moment.tzinfo is None:
        raise BackupError("backup clock must be timezone-aware")
    moment = moment.astimezone(UTC)
    name = moment.strftime("%Y%m%dT%H%M%SZ")
    created_at = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    root = settings.backups_dir
    with _backup_lock(root):
        dest = root / name
        if dest.exists() or dest.is_symlink():
            raise BackupError(f"backup {name} already exists")
        dest.mkdir(parents=False)
        try:
            os.chmod(dest, 0o700)
            db.backup_to(dest / "sparky_rats.db")
            os.chmod(dest / "sparky_rats.db", 0o600)
            points = vectors.export_snapshot(dest / "qdrant")
            os.chmod(dest / "qdrant" / "points.jsonl", 0o600)
            os.chmod(dest / "qdrant" / "collection.json", 0o600)
            _write_manifest(dest, created_at=created_at, points=points)
            _reject_secret_names(dest)
            removed = rotate_backups(root, retention_days=days, now=moment, keep=name)
        except Exception as exc:
            shutil.rmtree(dest, ignore_errors=True)
            if isinstance(exc, BackupError):
                raise
            if isinstance(exc, (OSError, ValueError)):
                raise BackupError(str(exc)) from exc
            raise
    return BackupReport(
        name=name,
        relative_path=f"backups/{name}",
        mode="api",
        points=points,
        removed=removed,
        created_at=created_at,
    )


def rotate_backups(
    root: Path,
    *,
    retention_days: int,
    now: datetime,
    keep: str,
) -> list[str]:
    if retention_days < 1:
        raise BackupError("retention_days must be at least 1")
    cutoff = now.astimezone(UTC) - timedelta(days=retention_days)
    removed: list[str] = []
    if not root.exists():
        return removed
    for child in sorted(root.iterdir()):
        if child.is_symlink() or not child.is_dir():
            continue
        if not STAMP_RE.fullmatch(child.name) or child.name == keep:
            continue
        stamp = datetime.strptime(child.name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        if stamp < cutoff:
            shutil.rmtree(child)
            removed.append(child.name)
    return removed


def list_backups(settings: Settings) -> list[dict[str, object]]:
    root = settings.backups_dir
    if not root.exists():
        return []
    found: list[dict[str, object]] = []
    for child in sorted(root.iterdir()):
        if child.is_symlink() or not child.is_dir() or not STAMP_RE.fullmatch(child.name):
            continue
        qdrant = child / "qdrant"
        manifest = child / "MANIFEST.txt"
        found.append(
            {
                "name": child.name,
                "path": f"backups/{child.name}",
                "has_sqlite": (child / "sparky_rats.db").is_file(),
                "has_vectors": (qdrant / "points.jsonl").is_file(),
                "has_raw_qdrant": (qdrant / "meta.json").is_file(),
                "manifest": manifest.read_text(encoding="utf-8") if manifest.is_file() else "",
            }
        )
    return found


def restore_vectors(vectors: VectorStore, settings: Settings, name: str) -> int:
    if not STAMP_RE.fullmatch(name):
        raise BackupError("backup name must be a UTC stamp")
    root = settings.backups_dir.resolve()
    src = (settings.backups_dir / name).resolve()
    if src.parent != root:
        raise BackupError("backup path escapes the backups directory")
    if not src.is_dir():
        raise BackupNotFound(f"backup {name} was not found")
    snapshot = src / "qdrant"
    if not (snapshot / "points.jsonl").is_file():
        raise BackupError("backup has no logical Qdrant snapshot")
    with _backup_lock(settings.backups_dir):
        try:
            return vectors.replace_from_snapshot(snapshot)
        except ValueError as exc:
            raise BackupError(str(exc)) from exc


def _write_manifest(dest: Path, *, created_at: str, points: int) -> None:
    text = "\n".join(
        (
            f"created_at: {created_at}",
            "mode: api",
            "sqlite: sparky_rats.db",
            "qdrant: points.jsonl",
            f"points: {points}",
            "master_key_included: false",
            "master_key: NOT INCLUDED",
            f"reminder: {REMINDER}",
            "",
        )
    )
    path = dest / "MANIFEST.txt"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)


def _reject_secret_names(dest: Path) -> None:
    for path in dest.rglob("*"):
        if path.name in {"master.key", "api_token", "mcp_token"}:
            raise BackupError(f"backup contains a credential file name: {path.name}")


@contextmanager
def _backup_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    fd = os.open(root / ".backup.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise BackupError("another backup is running") from exc
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
