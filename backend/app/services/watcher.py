"""In-process inbox observer (ADR-005).

The thread is started from the FastAPI lifespan. Parsing and embedding stay
in the job runner; this slice only proves the observer is running.
"""

from __future__ import annotations

from pathlib import Path

from app.inbox_observer import DirectoryObserver, start_inbox_observer


class InboxWatcher:
    def __init__(self, inbox: Path) -> None:
        self._inbox = inbox
        self._observer: DirectoryObserver | None = None

    def start(self) -> None:
        if self.running():
            return
        self._inbox.mkdir(parents=True, exist_ok=True)
        self._observer = start_inbox_observer(self._inbox)

    def stop(self) -> None:
        observer = self._observer
        self._observer = None
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=2)

    def running(self) -> bool:
        observer = self._observer
        return observer is not None and observer.is_alive()
