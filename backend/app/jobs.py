"""In-process job runner and server-sent events (ADR-014)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from app.db.database import Database
from app.db.repositories.jobs import TERMINAL_STATUSES, JobRecord, JobRepository

log = logging.getLogger(__name__)

JobHandler = Callable[["JobContext"], Awaitable[dict[str, Any]]]


class JobCancelled(Exception):
    """Raised by a handler that noticed a cancel request."""


class JobContext:
    def __init__(
        self,
        job: JobRecord,
        repo: JobRepository,
        publish: Callable[[JobRecord], None],
    ) -> None:
        self.job_id = job.id
        self.job_type = job.type
        self.payload = job.payload
        self._repo = repo
        self._publish = publish

    def progress(self, value: int, message: str) -> None:
        self._publish(self._repo.update_progress(self.job_id, value, message))

    def raise_if_cancelled(self) -> None:
        current = self._repo.get(self.job_id)
        if current is not None and current.cancel_requested:
            raise JobCancelled()


class JobRunner:
    def __init__(self, db: Database, *, general_concurrency: int = 4) -> None:
        self._repo = JobRepository(db)
        self._general_limit = general_concurrency
        self._handlers: dict[str, JobHandler] = {}
        self._llm_types: set[str] = set()
        self._general_inflight = 0
        self._llm_inflight = False
        self._stopped = False
        self._wake = asyncio.Event()
        self._subscribers: dict[str, set[asyncio.Queue[JobRecord]]] = defaultdict(set)
        self._tasks: set[asyncio.Task[None]] = set()

    def register(self, job_type: str, handler: JobHandler, *, llm: bool = False) -> None:
        self._handlers[job_type] = handler
        if llm:
            self._llm_types.add(job_type)
        else:
            self._llm_types.discard(job_type)

    def enqueue(self, job_type: str, payload: dict[str, Any]) -> str:
        job = self._repo.insert(job_type, payload)
        self._wake.set()
        return job.id

    def get(self, job_id: str) -> JobRecord | None:
        return self._repo.get(job_id)

    def request_cancel(self, job_id: str) -> JobRecord | None:
        job = self._repo.request_cancel(job_id)
        if job is not None:
            self.publish(job)
            self._wake.set()
        return job

    def subscribe(self, job_id: str) -> asyncio.Queue[JobRecord]:
        queue: asyncio.Queue[JobRecord] = asyncio.Queue()
        self._subscribers[job_id].add(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue[JobRecord]) -> None:
        listeners = self._subscribers.get(job_id)
        if listeners is None:
            return
        listeners.discard(queue)
        if not listeners:
            self._subscribers.pop(job_id, None)

    def publish(self, job: JobRecord) -> None:
        for queue in list(self._subscribers.get(job.id, ())):
            queue.put_nowait(job)

    async def loop(self) -> None:
        while not self._stopped:
            self._dispatch_available()
            if self._stopped:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.2)
            except TimeoutError:
                pass
            self._wake.clear()

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=2)
        self._repo.mark_interrupted()

    def _dispatch_available(self) -> None:
        if self._stopped:
            return
        if not self._llm_inflight:
            job = self._repo.claim_next(llm_types=self._llm_types, want_llm=True)
            if job is not None:
                self._llm_inflight = True
                self.publish(job)
                self._spawn(job, llm=True)
        while not self._stopped and self._general_inflight < self._general_limit:
            job = self._repo.claim_next(llm_types=self._llm_types, want_llm=False)
            if job is None:
                return
            self._general_inflight += 1
            self.publish(job)
            self._spawn(job, llm=False)

    def _spawn(self, job: JobRecord, *, llm: bool) -> None:
        task = asyncio.create_task(self._run(job, llm=llm))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job: JobRecord, *, llm: bool) -> None:
        try:
            handler = self._handlers.get(job.type)
            if handler is None:
                finished = self._repo.finish_failed(job.id, f"no handler for {job.type}")
                self.publish(finished)
                return
            ctx = JobContext(job, self._repo, self.publish)
            try:
                result = await handler(ctx)
            except asyncio.CancelledError:
                finished = self._repo.finish_failed(job.id, "interrupted by process restart")
                self.publish(finished)
                raise
            except JobCancelled:
                finished = self._repo.finish_cancelled(job.id)
                self.publish(finished)
                return
            except Exception as exc:
                log.exception("job %s failed", job.id)
                finished = self._repo.finish_failed(job.id, f"{type(exc).__name__}: {exc}")
                self.publish(finished)
                return
            current = self._repo.get(job.id)
            if current is not None and current.cancel_requested:
                finished = self._repo.finish_cancelled(job.id)
                self.publish(finished)
                return
            if not isinstance(result, dict):
                finished = self._repo.finish_failed(job.id, "handler returned a non-object result")
                self.publish(finished)
                return
            finished = self._repo.finish_done(job.id, result)
            self.publish(finished)
        finally:
            if llm:
                self._llm_inflight = False
            else:
                self._general_inflight -= 1
            self._wake.set()


def format_sse(job: JobRecord) -> str:
    payload = json.dumps(job.public_dict(), ensure_ascii=False)
    return f"event: job\ndata: {payload}\n\n"


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES
