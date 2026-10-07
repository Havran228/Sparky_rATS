from __future__ import annotations

from app.services.watcher import InboxWatcher


def test_watcher_starts_and_stops(tmp_path) -> None:
    watcher = InboxWatcher(tmp_path / "inbox")
    watcher.start()
    try:
        assert watcher.running() is True
        assert (tmp_path / "inbox").is_dir()
    finally:
        watcher.stop()
    assert watcher.running() is False
