from __future__ import annotations

import asyncio
import contextlib

import pytest
from fastapi.testclient import TestClient

from app.db.database import Database
from app.db.repositories.jobs import RESTART_ERROR, JobRepository
from app.jobs import JobCancelled, JobContext, JobRunner


def _db(settings) -> Database:
    database = Database(settings.db_path)
    database.migrate()
    return database


@pytest.mark.asyncio
async def test_llm_jobs_are_serialized_and_general_jobs_overlap(settings) -> None:
    db = _db(settings)
    runner = JobRunner(db, general_concurrency=2)
    llm_current = 0
    llm_peak = 0
    general_current = 0
    general_peak = 0
    general_both = asyncio.Event()

    async def llm_handler(ctx: JobContext) -> dict[str, object]:
        nonlocal llm_current, llm_peak
        llm_current += 1
        llm_peak = max(llm_peak, llm_current)
        # Longer than the runner poll so a second LLM job can be claimed if the
        # single-flight guard is missing.
        await asyncio.sleep(0.45)
        llm_current -= 1
        return {"n": ctx.payload["n"]}

    async def general_handler(ctx: JobContext) -> dict[str, object]:
        nonlocal general_current, general_peak
        general_current += 1
        general_peak = max(general_peak, general_current)
        if general_current >= 2:
            general_both.set()
        await asyncio.wait_for(general_both.wait(), timeout=1)
        general_current -= 1
        return {"n": ctx.payload["n"]}

    runner.register("extract", llm_handler, llm=True)
    runner.register("index", general_handler, llm=False)
    loop = asyncio.create_task(runner.loop())
    llm_ids = [runner.enqueue("extract", {"n": index}) for index in (1, 2)]
    general_ids = [runner.enqueue("index", {"n": index}) for index in (1, 2)]
    for job_id in llm_ids + general_ids:
        await _wait(runner, job_id, "DONE")
    await _shutdown(runner, loop)
    db.close()

    assert llm_peak == 1
    assert general_peak == 2


@pytest.mark.asyncio
async def test_restart_marks_running_jobs_failed(settings) -> None:
    db = _db(settings)
    repo = JobRepository(db)
    job = repo.insert("extract", {"n": 1})
    db.execute("UPDATE jobs SET status = 'RUNNING' WHERE id = ?", (job.id,))
    done = repo.insert("extract", {"n": 2})
    db.execute(
        """
        UPDATE jobs
        SET status = 'DONE', result = '{}', finished_at = created_at
        WHERE id = ?
        """,
        (done.id,),
    )
    changed = repo.mark_interrupted()
    failed = repo.get(job.id)
    untouched = repo.get(done.id)
    db.close()

    assert changed == 1
    assert failed is not None and failed.status == "FAILED"
    assert failed.error == RESTART_ERROR
    assert untouched is not None and untouched.status == "DONE"


@pytest.mark.asyncio
async def test_cancel_queued_and_running(settings) -> None:
    db = _db(settings)
    runner = JobRunner(db)
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(ctx: JobContext) -> dict[str, object]:
        started.set()
        await release.wait()
        ctx.raise_if_cancelled()
        return {"ok": True}

    runner.register("slow", handler, llm=False)
    queued_id = runner.enqueue("slow", {})
    queued = runner.request_cancel(queued_id)
    assert queued is not None and queued.status == "CANCELLED"

    loop = asyncio.create_task(runner.loop())
    running_id = runner.enqueue("slow", {})
    await asyncio.wait_for(started.wait(), timeout=1)
    runner.request_cancel(running_id)
    release.set()
    await _wait(runner, running_id, "CANCELLED")
    await _shutdown(runner, loop)
    db.close()


@pytest.mark.asyncio
async def test_handler_can_raise_job_cancelled(settings) -> None:
    db = _db(settings)
    runner = JobRunner(db)

    async def handler(ctx: JobContext) -> dict[str, object]:
        raise JobCancelled()

    runner.register("slow", handler)
    loop = asyncio.create_task(runner.loop())
    job_id = runner.enqueue("slow", {})
    await _wait(runner, job_id, "CANCELLED")
    await _shutdown(runner, loop)
    db.close()


def test_unknown_handler_fails_over_http(
    client: TestClient, auth_header: dict[str, str]
) -> None:
    job_id = client.app.state.job_runner.enqueue("missing", {"x": 1})
    body = _poll(client, job_id, auth_header, "FAILED")
    assert "no handler" in body["error"]


def test_sse_replays_terminal_job(client: TestClient, auth_header: dict[str, str]) -> None:
    async def handler(ctx: JobContext) -> dict[str, object]:
        ctx.progress(25, "working")
        return {"echo": ctx.payload["value"]}

    client.app.state.job_runner.register("echo", handler)
    job_id = client.app.state.job_runner.enqueue("echo", {"value": "ping"})
    _poll(client, job_id, auth_header, "DONE")
    with client.stream("GET", f"/jobs/{job_id}/events", headers=auth_header) as response:
        text = "".join(response.iter_text())
    assert response.status_code == 200
    assert "event: job" in text
    assert "DONE" in text
    assert "ping" in text


def test_job_routes_require_auth_and_report_missing(
    client: TestClient, auth_header: dict[str, str]
) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/jobs/{missing}").status_code == 401
    assert client.get(f"/jobs/{missing}", headers=auth_header).status_code == 404
    assert client.post(f"/jobs/{missing}/cancel", headers=auth_header).status_code == 404


def _poll(
    client: TestClient,
    job_id: str,
    auth_header: dict[str, str],
    status: str,
) -> dict[str, object]:
    import time

    deadline = time.monotonic() + 3
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=auth_header)
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"DONE", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.02)
    assert last["status"] == status
    return last


async def _shutdown(runner: JobRunner, loop: asyncio.Task[None]) -> None:
    await runner.stop()
    loop.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop


async def _wait(runner: JobRunner, job_id: str, status: str) -> None:
    deadline = asyncio.get_running_loop().time() + 3
    last = None
    while asyncio.get_running_loop().time() < deadline:
        last = runner.get(job_id)
        if last is not None and last.status in {"DONE", "FAILED", "CANCELLED"}:
            break
        await asyncio.sleep(0.01)
    assert last is not None and last.status == status
