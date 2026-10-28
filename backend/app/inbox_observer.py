"""Watchdog observer isolated from the strictly typed watcher service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


class DirectoryObserver(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


class _InboxHandler(FileSystemEventHandler):
    def __init__(self, on_path: Callable[[str], None] | None) -> None:
        super().__init__()
        self._on_path = on_path

    def on_created(self, event: FileSystemEvent) -> None:
        self._note(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._note(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._note(event)

    def _note(self, event: FileSystemEvent) -> None:
        callback = self._on_path
        if callback is None or event.is_directory:
            return
        raw = getattr(event, "dest_path", None) or event.src_path
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        if isinstance(raw, str):
            callback(raw)


def start_inbox_observer(
    path: Path,
    on_path: Callable[[str], None] | None = None,
) -> DirectoryObserver:
    path.mkdir(parents=True, exist_ok=True)
    observer: Any = Observer()
    observer.daemon = True
    observer.schedule(_InboxHandler(on_path), str(path), recursive=False)
    observer.start()
    return cast(DirectoryObserver, observer)
