from __future__ import annotations

import os
import sqlite3
import subprocess
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKUP = ROOT / "scripts" / "backup.sh"
ENTRY = ROOT / "scripts" / "backup-entry.sh"
RESTORE = ROOT / "scripts" / "restore.sh"
BENCH = ROOT / "scripts" / "benchmark_models.sh"


def _env(data: Path, **extra: str) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TZ": "UTC",
        "SPARKY_DATA_DIR": str(data),
    }
    env.update(extra)
    return env


def _seed_db(data: Path, note: str) -> None:
    data.mkdir(parents=True, exist_ok=True)
    db = data / "sparky_rats.db"
    connection = sqlite3.connect(db)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS example (note TEXT)")
        connection.execute("DELETE FROM example")
        connection.execute("INSERT INTO example (note) VALUES (?)", (note,))
        connection.commit()
    finally:
        connection.close()


def _note(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute("SELECT note FROM example").fetchone()
    finally:
        connection.close()
    assert row is not None
    return str(row[0])


def _run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_shell_scripts_parse() -> None:
    for script in (BACKUP, ENTRY, RESTORE, BENCH):
        parsed = subprocess.run(["bash", "-n", str(script)], check=False)
        assert parsed.returncode == 0
    assert _run(BACKUP, _env(Path("/tmp")), "--help").returncode == 0
    assert _run(RESTORE, _env(Path("/tmp")), "--help").returncode == 2
    assert _run(BENCH, _env(Path("/tmp")), "--help").returncode == 0


def test_direct_backup_rotates_and_skips_the_master_key(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _seed_db(data, "keep-me")
    qdrant = data / "qdrant"
    qdrant.mkdir()
    (qdrant / "meta.json").write_text('{"ok":true}\n', encoding="utf-8")
    (data / "credentials").mkdir()
    (data / "credentials" / "master.key").write_text("SUPER-SECRET-KEY\n", encoding="utf-8")
    (data / "backups" / "20000101T000000Z").mkdir(parents=True)
    (data / "backups" / "notes").mkdir()
    (data / "backups" / "29990101T000000Z").mkdir()
    result = _run(
        BACKUP,
        _env(data, SPARKY_BACKUP_DIRECT="1", SPARKY_BACKUP_RETENTION_DAYS="14"),
    )
    assert result.returncode == 0, result.stderr
    stamps = [
        path
        for path in (data / "backups").iterdir()
        if path.is_dir() and path.name[:1].isdigit() and path.name != "29990101T000000Z"
    ]
    assert len(stamps) == 1
    dest = stamps[0]
    assert _note(dest / "sparky_rats.db") == "keep-me"
    assert (dest / "qdrant" / "meta.json").is_file()
    assert not (dest / "qdrant" / ".lock").exists()
    manifest = (dest / "MANIFEST.txt").read_text(encoding="utf-8")
    assert "master_key: NOT INCLUDED" in manifest
    assert "qdrant: directory" in manifest
    for path in dest.rglob("*"):
        if path.is_file():
            assert "SUPER-SECRET-KEY" not in path.read_text(encoding="utf-8", errors="ignore")
    assert not (data / "backups" / "20000101T000000Z").exists()
    assert (data / "backups" / "notes").is_dir()
    assert (data / "backups" / "29990101T000000Z").is_dir()


def test_direct_backup_leaves_a_locked_qdrant_store(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _seed_db(data, "keep-me")
    qdrant = data / "qdrant"
    qdrant.mkdir()
    lock = qdrant / ".lock"
    lock.write_text("held\n", encoding="utf-8")
    (qdrant / "meta.json").write_text('{"ok":true}\n', encoding="utf-8")
    holder = subprocess.Popen(["flock", str(lock), "sleep", "30"])
    try:
        time.sleep(0.2)
        result = _run(BACKUP, _env(data, SPARKY_BACKUP_DIRECT="1"))
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert result.returncode == 3, result.stderr
    dest = next(path for path in (data / "backups").iterdir() if path.is_dir())
    assert (dest / "sparky_rats.db").is_file()
    assert "skipped-locked" in (dest / "MANIFEST.txt").read_text(encoding="utf-8")
    assert not (dest / "qdrant" / "meta.json").exists()


def test_api_http_error_does_not_fall_back_to_a_direct_copy(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _seed_db(data, "keep-me")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.05},
        daemon=True,
    )
    thread.start()
    try:
        port = server.server_address[1]
        result = _run(
            BACKUP,
            _env(
                data,
                SPARKY_API_BASE=f"http://127.0.0.1:{port}",
                SPARKY_API_TOKEN="wrong-token",
            ),
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
    assert result.returncode == 1
    assert "HTTP 401" in result.stderr
    assert not (data / "backups").exists()


def test_unreachable_api_falls_back_to_a_direct_backup(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _seed_db(data, "keep-me")
    result = _run(
        BACKUP,
        _env(
            data,
            SPARKY_API_BASE="http://127.0.0.1:9",
            SPARKY_API_TOKEN="dev-token",
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "falling back" in result.stderr
    assert any((data / "backups").glob("*/sparky_rats.db"))


def test_restore_replaces_sqlite_and_a_raw_qdrant_directory(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _seed_db(data, "old")
    backup = data / "backups" / "20261028T031500Z"
    backup.mkdir(parents=True)
    _seed_db(backup, "new")
    (backup / "MANIFEST.txt").write_text("mode: direct\n", encoding="utf-8")
    (backup / "qdrant").mkdir()
    (backup / "qdrant" / "meta.json").write_text('{"from":"backup"}\n', encoding="utf-8")
    env = _env(data)
    plan = _run(RESTORE, env, str(backup))
    assert plan.returncode == 0, plan.stderr
    assert _note(data / "sparky_rats.db") == "old"
    applied = _run(RESTORE, env, "--yes", "20261028T031500Z")
    assert applied.returncode == 0, applied.stderr
    assert _note(data / "sparky_rats.db") == "new"
    assert "backup" in (data / "qdrant" / "meta.json").read_text(encoding="utf-8")


def test_restore_refuses_a_locked_store(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _seed_db(data, "old")
    qdrant = data / "qdrant"
    qdrant.mkdir()
    lock = qdrant / ".lock"
    lock.write_text("held\n", encoding="utf-8")
    backup = data / "backups" / "20261028T031500Z"
    backup.mkdir(parents=True)
    _seed_db(backup, "new")
    (backup / "MANIFEST.txt").write_text("mode: direct\n", encoding="utf-8")
    holder = subprocess.Popen(["flock", str(lock), "sleep", "30"])
    try:
        time.sleep(0.2)
        result = _run(RESTORE, _env(data), "--yes", str(backup))
    finally:
        holder.kill()
        holder.wait(timeout=5)
    assert result.returncode == 1
    assert "locked" in result.stderr.lower()
    assert _note(data / "sparky_rats.db") == "old"


def test_backup_schedule_runs_once_per_day(tmp_path: Path) -> None:
    data = tmp_path / "data"
    marker = tmp_path / "ran"
    bin_path = tmp_path / "backup-bin.sh"
    bin_path.write_text(f"#!/bin/bash\ntouch {marker}\n", encoding="utf-8")
    bin_path.chmod(0o755)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    (data / "backups").mkdir(parents=True)
    (data / "backups" / ".last_schedule").write_text(today + "\n", encoding="utf-8")
    skipped = _run(
        ENTRY,
        _env(
            data,
            SPARKY_BACKUP_BIN=str(bin_path),
            SPARKY_BACKUP_AT="00:00",
            SPARKY_BACKUP_TICKS="1",
            SPARKY_BACKUP_POLL_SECONDS="0",
        ),
    )
    assert skipped.returncode == 0, skipped.stderr
    assert not marker.exists()

    (data / "backups" / ".last_schedule").unlink()
    ran = _run(
        ENTRY,
        _env(
            data,
            SPARKY_BACKUP_BIN=str(bin_path),
            SPARKY_BACKUP_AT="00:00",
            SPARKY_BACKUP_TICKS="1",
            SPARKY_BACKUP_POLL_SECONDS="0",
        ),
    )
    assert ran.returncode == 0, ran.stderr
    assert marker.is_file()
    assert (data / "backups" / ".last_schedule").read_text(encoding="utf-8").strip() == today

    bad = _run(ENTRY, _env(data, SPARKY_BACKUP_AT="24:00", SPARKY_BACKUP_TICKS="1"))
    assert bad.returncode == 1
