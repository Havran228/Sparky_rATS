"""Watchdog observer isolated from the strictly typed watcher service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, cast

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class DirectoryObserver(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


class IdleHandler(FileSystemEventHandler):
    """Slice 0 keeps the observer alive. Slice 2 handles new files."""


def start_inbox_observer(path: Path) -> DirectoryObserver:
    path.mkdir(parents=True, exist_ok=True)
    observer: Any = Observer()
    observer.daemon = True
    observer.schedule(IdleHandler(), str(path), recursive=False)
    observer.start()
    return cast(DirectoryObserver, observer)
