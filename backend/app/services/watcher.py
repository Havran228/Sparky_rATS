"""In-process inbox observer (ADR-005).

Stable files are hashed, moved to ``bundles/_inbox``, and handed to the job
runner. Parsing stays in the ingest_file job.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path

from app.inbox_observer import DirectoryObserver, start_inbox_observer

log = logging.getLogger(__name__)

FileReady = Callable[[Path, str, str], None]
_SKIP_PREFIXES = (".", "~")
_SKIP_SUFFIXES = (".tmp", ".part", ".crdownload")


class InboxWatcher:
    def __init__(
        self,
        inbox: Path,
        *,
        dest: Path | None = None,
        on_file: FileReady | None = None,
        debounce_seconds: float = 1.0,
    ) -> None:
        self._inbox = inbox
        self._dest = dest
        self._on_file = on_file
        self._debounce = debounce_seconds
        self._observer: DirectoryObserver | None = None
        self._stop = threading.Event()
        self._timer: threading.Thread | None = None
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[int, float]] = {}

    def start(self) -> None:
        if self.running():
            return
        self._inbox.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        if self._on_file is not None:
            self._timer = threading.Thread(target=self._pump, name="inbox-debounce", daemon=True)
            self._timer.start()
        self._observer = start_inbox_observer(self._inbox, self.note_path)

    def stop(self) -> None:
        self._stop.set()
        timer = self._timer
        self._timer = None
        if timer is not None:
            timer.join(timeout=2)
        observer = self._observer
        self._observer = None
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=2)

    def running(self) -> bool:
        observer = self._observer
        return observer is not None and observer.is_alive()

    def note_path(self, raw: str) -> None:
        path = Path(raw)
        if not _interesting(path):
            return
        try:
            size = path.stat().st_size
        except OSError:
            return
        with self._lock:
            current = self._pending.get(str(path))
            if current is not None and current[0] == size:
                return
            self._pending[str(path)] = (size, time.monotonic())

    def poll(self) -> None:
        now = time.monotonic()
        ready: list[Path] = []
        with self._lock:
            for key, (size, seen) in list(self._pending.items()):
                path = Path(key)
                if not path.is_file():
                    self._pending.pop(key, None)
                    continue
                try:
                    current_size = path.stat().st_size
                except OSError:
                    continue
                if current_size != size:
                    self._pending[key] = (current_size, now)
                    continue
                if now - seen < self._debounce:
                    continue
                self._pending.pop(key, None)
                ready.append(path)
        for path in ready:
            self._accept(path)

    def _pump(self) -> None:
        while not self._stop.wait(0.1):
            try:
                self.poll()
            except Exception:
                log.exception("inbox poll failed")

    def _accept(self, path: Path) -> None:
        dest = self._dest
        callback = self._on_file
        if dest is None or callback is None:
            return
        try:
            stored, original, digest = accept_inbox_file(path, dest)
        except OSError:
            log.exception("inbox move failed for %s", path)
            return
        try:
            callback(stored, original, digest)
        except Exception:
            log.exception("inbox callback failed for %s", stored)


def accept_inbox_file(source: Path, dest_dir: Path) -> tuple[Path, str, str]:
    data = source.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    original = source.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{digest}{source.suffix.lower()}"
    if target.resolve() == source.resolve():
        return target, original, digest
    if target.exists():
        source.unlink()
        return target, original, digest
    shutil.move(str(source), str(target))
    return target, original, digest


def _interesting(path: Path) -> bool:
    name = path.name
    if not name or name.startswith(_SKIP_PREFIXES) or name.endswith(_SKIP_SUFFIXES):
        return False
    return True
