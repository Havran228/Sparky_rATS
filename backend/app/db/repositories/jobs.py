"""Jobs table access (ADR-014)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database

TERMINAL_STATUSES = frozenset({"DONE", "FAILED", "CANCELLED"})
RESTART_ERROR = "interrupted by process restart"


@dataclass(frozen=True)
class JobRecord:
    id: str
    type: str
    status: str
    progress: int
    progress_message: str
    payload: dict[str, object]
    result: dict[str, object] | None
    error: str | None
    cancel_requested: bool
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _loads_object(raw: str | None) -> dict[str, object] | None:
    if raw is None:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("expected a JSON object")
    return parsed


def _row_to_job(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=str(row["id"]),
        type=str(row["type"]),
        status=str(row["status"]),
        progress=int(row["progress"]),
        progress_message=str(row["progress_message"]),
        payload=_loads_object(str(row["payload"])) or {},
        result=_loads_object(row["result"]),
        error=None if row["error"] is None else str(row["error"]),
        cancel_requested=bool(row["cancel_requested"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=None if row["started_at"] is None else str(row["started_at"]),
        finished_at=None if row["finished_at"] is None else str(row["finished_at"]),
    )


class JobRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, job_type: str, payload: dict[str, object]) -> JobRecord:
        if not isinstance(payload, dict):
            raise TypeError("job payload must be a JSON object")
        now = utc_now()
        job_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO jobs (
                id, type, status, progress, progress_message, payload,
                cancel_requested, created_at, updated_at
            ) VALUES (?, ?, 'QUEUED', 0, '', ?, 0, ?, ?)
            """,
            (job_id, job_type, json.dumps(payload, ensure_ascii=False), now, now),
        )
        job = self.get(job_id)
        if job is None:
            raise RuntimeError("inserted job disappeared")
        return job

    def get(self, job_id: str) -> JobRecord | None:
        row = self._db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return None if row is None else _row_to_job(row)

    def claim_next(self, *, llm_types: set[str], want_llm: bool) -> JobRecord | None:
        clause, params = _type_clause(llm_types, want_llm=want_llm)
        if clause is None:
            return None
        now = utc_now()
        with self._db.immediate() as conn:
            row = conn.execute(
                f"""
                SELECT id FROM jobs
                WHERE status = 'QUEUED' {clause}
                ORDER BY created_at, id
                LIMIT 1
                """,
                params,
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["id"])
            updated = conn.execute(
                """
                UPDATE jobs
                SET status = 'RUNNING', started_at = ?, updated_at = ?, progress_message = 'running'
                WHERE id = ? AND status = 'QUEUED'
                """,
                (now, now, job_id),
            )
            if updated.rowcount != 1:
                return None
            claimed = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return None if claimed is None else _row_to_job(claimed)

    def update_progress(self, job_id: str, progress: int, message: str) -> JobRecord:
        if not 0 <= progress <= 100:
            raise ValueError("progress must be between 0 and 100")
        now = utc_now()
        self._db.execute(
            """
            UPDATE jobs
            SET progress = ?, progress_message = ?, updated_at = ?
            WHERE id = ? AND status = 'RUNNING'
            """,
            (progress, message, now, job_id),
        )
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def finish_done(self, job_id: str, result: dict[str, object]) -> JobRecord:
        return self._finish(job_id, status="DONE", result=result, error=None)

    def finish_failed(self, job_id: str, error: str) -> JobRecord:
        return self._finish(job_id, status="FAILED", result=None, error=error)

    def finish_cancelled(self, job_id: str) -> JobRecord:
        return self._finish(job_id, status="CANCELLED", result=None, error=None)

    def request_cancel(self, job_id: str) -> JobRecord | None:
        now = utc_now()
        with self._db.immediate() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            status = str(row["status"])
            if status == "QUEUED":
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'CANCELLED', cancel_requested = 1, finished_at = ?, updated_at = ?,
                        progress_message = 'cancelled'
                    WHERE id = ? AND status = 'QUEUED'
                    """,
                    (now, now, job_id),
                )
            elif status == "RUNNING":
                conn.execute(
                    """
                    UPDATE jobs
                    SET cancel_requested = 1, updated_at = ?
                    WHERE id = ? AND status = 'RUNNING'
                    """,
                    (now, job_id),
                )
        return self.get(job_id)

    def mark_interrupted(self) -> int:
        now = utc_now()
        return self._db.execute(
            """
            UPDATE jobs
            SET status = 'FAILED', error = ?, finished_at = ?, updated_at = ?,
                progress_message = ?
            WHERE status = 'RUNNING'
            """,
            (RESTART_ERROR, now, now, RESTART_ERROR),
        )

    def _finish(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, object] | None,
        error: str | None,
    ) -> JobRecord:
        now = utc_now()
        result_text = None if result is None else json.dumps(result, ensure_ascii=False)
        progress = 100 if status == "DONE" else None
        if progress is None:
            self._db.execute(
                """
                UPDATE jobs
                SET status = ?, result = ?, error = ?, finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'RUNNING'
                """,
                (status, result_text, error, now, now, job_id),
            )
        else:
            self._db.execute(
                """
                UPDATE jobs
                SET status = ?, result = ?, error = ?, progress = ?, finished_at = ?, updated_at = ?
                WHERE id = ? AND status = 'RUNNING'
                """,
                (status, result_text, error, progress, now, now, job_id),
            )
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job


def _type_clause(llm_types: set[str], *, want_llm: bool) -> tuple[str | None, tuple[str, ...]]:
    if want_llm:
        if not llm_types:
            return None, ()
        names = tuple(sorted(llm_types))
        marks = ", ".join("?" for _ in names)
        return f"AND type IN ({marks})", names
    if not llm_types:
        return "", ()
    names = tuple(sorted(llm_types))
    marks = ", ".join("?" for _ in names)
    return f"AND type NOT IN ({marks})", names
