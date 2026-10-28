"""Fernet-encrypted credential blobs and the master key file (ADR-012)."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.clock import utc_now
from app.config import Settings
from app.db.database import Database


class SecretsError(RuntimeError):
    """The master key or a stored payload cannot be used."""


def load_fernet(settings: Settings) -> Fernet:
    if settings.master_key:
        return _fernet_from_text(settings.master_key)
    path = settings.master_key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return _fernet_from_text(path.read_text(encoding="utf-8"))
    key = Fernet.generate_key()
    _write_secret_file(path, key.decode("ascii"))
    return Fernet(key)


def ensure_bearer(
    store: SecretsStore,
    *,
    service: str,
    provided: str | None,
    file_path: Path | None,
) -> str:
    if provided:
        store.put(service, {"token": provided})
        return provided
    existing = store.get(service)
    token = existing.get("token") if existing else None
    if isinstance(token, str) and token:
        if file_path is not None and not file_path.exists():
            _write_secret_file(file_path, token)
        return token
    generated = secrets.token_urlsafe(32)
    store.put(service, {"token": generated})
    if file_path is not None:
        _write_secret_file(file_path, generated)
    return generated


class SecretsStore:
    def __init__(self, db: Database, fernet: Fernet) -> None:
        self._db = db
        self._fernet = fernet

    def put(self, service_name: str, payload: dict[str, object]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        encrypted = self._fernet.encrypt(raw).decode("ascii")
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO system_credentials (service_name, payload_encrypted, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(service_name) DO UPDATE SET
                payload_encrypted = excluded.payload_encrypted,
                updated_at = excluded.updated_at
            """,
            (service_name, encrypted, now),
        )

    def get(self, service_name: str) -> dict[str, object] | None:
        row = self._db.query_one(
            "SELECT payload_encrypted FROM system_credentials WHERE service_name = ?",
            (service_name,),
        )
        if row is None:
            return None
        try:
            raw = self._fernet.decrypt(str(row["payload_encrypted"]).encode("ascii"))
        except InvalidToken as exc:
            raise SecretsError(f"cannot decrypt credentials for {service_name}") from exc
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise SecretsError(f"credential payload for {service_name} is not an object")
        return parsed

    def delete(self, service_name: str) -> bool:
        return (
            self._db.execute(
                "DELETE FROM system_credentials WHERE service_name = ?",
                (service_name,),
            )
            > 0
        )


def _fernet_from_text(value: str) -> Fernet:
    try:
        return Fernet(value.strip().encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise SecretsError("SPARKY_MASTER_KEY or master.key is not a valid Fernet key") from exc


def _write_secret_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")
