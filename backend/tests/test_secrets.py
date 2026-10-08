from __future__ import annotations

import os

import pytest
from cryptography.fernet import Fernet

from app.db.database import Database
from app.secrets_store import SecretsError, SecretsStore, ensure_bearer, load_fernet


def test_master_key_file_is_created_mode_0600(tmp_path, monkeypatch) -> None:
    from tests.conftest import make_settings

    settings = make_settings(tmp_path, monkeypatch, master_key=None)
    settings.data_dir.mkdir(parents=True)
    fernet = load_fernet(settings)
    mode = settings.master_key_path.stat().st_mode & 0o777
    assert mode == 0o600
    assert isinstance(fernet, Fernet)
    again = load_fernet(settings)
    token = again.encrypt(b"ok")
    assert fernet.decrypt(token) == b"ok"


def test_round_trip_and_wrong_key(settings) -> None:
    db = Database(settings.db_path)
    db.migrate()
    store = SecretsStore(db, load_fernet(settings))
    store.put("google", {"refresh_token": "abc", "scopes": ["gmail.readonly"]})
    assert store.get("google") == {"refresh_token": "abc", "scopes": ["gmail.readonly"]}
    assert store.get("missing") is None

    other = SecretsStore(db, Fernet(Fernet.generate_key()))
    with pytest.raises(SecretsError):
        other.get("google")
    db.close()


def test_generated_bearer_is_stable_and_written_0600(tmp_path, monkeypatch) -> None:
    from tests.conftest import make_settings

    settings = make_settings(tmp_path, monkeypatch, api_token=None, mcp_token=None)
    db = Database(settings.db_path)
    db.migrate()
    store = SecretsStore(db, load_fernet(settings))
    path = settings.credentials_dir / "api_token"
    first = ensure_bearer(store, service="api_bearer", provided=None, file_path=path)
    second = ensure_bearer(store, service="api_bearer", provided=None, file_path=path)
    mode = path.stat().st_mode & 0o777
    db.close()

    assert first == second
    assert mode == 0o600
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert path.read_text(encoding="utf-8").strip() == first


def test_invalid_master_key_is_rejected(tmp_path, monkeypatch) -> None:
    from tests.conftest import make_settings

    settings = make_settings(tmp_path, monkeypatch, master_key="not-a-key")
    with pytest.raises(SecretsError):
        load_fernet(settings)
