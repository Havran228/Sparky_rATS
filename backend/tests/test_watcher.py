from __future__ import annotations

import hashlib
import time

from app.services.watcher import InboxWatcher, accept_inbox_file


def test_watcher_starts_and_stops(tmp_path) -> None:
    watcher = InboxWatcher(tmp_path / "inbox")
    watcher.start()
    try:
        assert watcher.running() is True
        assert (tmp_path / "inbox").is_dir()
    finally:
        watcher.stop()
    assert watcher.running() is False


def test_accept_inbox_file_hashes_and_moves(tmp_path) -> None:
    source = tmp_path / "inbox" / "cv.md"
    source.parent.mkdir()
    source.write_text("hello", encoding="utf-8")
    stored, original, digest = accept_inbox_file(source, tmp_path / "staged")
    assert original == "cv.md"
    assert digest == hashlib.sha256(b"hello").hexdigest()
    assert stored.read_text(encoding="utf-8") == "hello"
    assert not source.exists()
    again = tmp_path / "inbox" / "copy.md"
    again.write_text("hello", encoding="utf-8")
    second, _, _ = accept_inbox_file(again, tmp_path / "staged")
    assert second == stored
    assert not again.exists()


def test_poll_releases_a_stable_file(tmp_path) -> None:
    inbox = tmp_path / "inbox"
    source = inbox / "note.txt"
    inbox.mkdir()
    source.write_text("alpha", encoding="utf-8")
    seen: list[str] = []
    watcher = InboxWatcher(
        inbox,
        dest=tmp_path / "staged",
        on_file=lambda path, original, digest: seen.append(original),
        debounce_seconds=0,
    )
    watcher.note_path(str(source))
    watcher.poll()
    assert seen == ["note.txt"]


def test_running_watcher_picks_up_a_drop(tmp_path) -> None:
    inbox = tmp_path / "inbox"
    seen: list[str] = []
    watcher = InboxWatcher(
        inbox,
        dest=tmp_path / "staged",
        on_file=lambda path, original, digest: seen.append(original),
        debounce_seconds=0.05,
    )
    watcher.start()
    try:
        (inbox / "drop.md").write_text("beta", encoding="utf-8")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not seen:
            time.sleep(0.05)
    finally:
        watcher.stop()
    assert seen == ["drop.md"]
