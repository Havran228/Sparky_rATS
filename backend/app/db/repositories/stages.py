"""Pipeline stages. The project's current_status is derived from these (ADR-017)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database

DEFAULT_PIPELINE: tuple[tuple[str, str], ...] = (
    ("New", "NEW"),
    ("CV draft", "CV_DRAFT"),
    ("Submitted", "SUBMITTED"),
    ("Screening", "SCREENING"),
    ("Interview", "INTERVIEW"),
    ("Offer", "OFFER"),
)

PIPELINE_COLUMNS: tuple[str, ...] = tuple(code for _, code in DEFAULT_PIPELINE)
TERMINAL_COLUMNS: tuple[str, ...] = ("ON_HOLD", "REJECTED", "WITHDRAWN", "GHOSTED")
PROGRESS_VALUES: frozenset[str] = frozenset(
    {"PENDING", "CURRENT", "DONE", *TERMINAL_COLUMNS}
)
COLUMN_LABELS: dict[str, str] = {
    "NEW": "New",
    "CV_DRAFT": "CV draft",
    "SUBMITTED": "Submitted",
    "SCREENING": "Screening",
    "INTERVIEW": "Interview",
    "OFFER": "Offer",
    "ON_HOLD": "On hold",
    "REJECTED": "Rejected",
    "WITHDRAWN": "Withdrawn",
    "GHOSTED": "Ghosted",
}


@dataclass(frozen=True)
class StageRecord:
    id: str
    project_id: str
    name: str
    status: str
    progress: str
    position: int
    notes: str | None
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "position": self.position,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class StageRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def list_for_project(self, project_id: str) -> list[StageRecord]:
        rows = self._db.query(
            """
            SELECT * FROM project_stages
            WHERE project_id = ?
            ORDER BY position, id
            """,
            (project_id,),
        )
        return [_row(row) for row in rows]

    def get(self, stage_id: str) -> StageRecord | None:
        row = self._db.query_one("SELECT * FROM project_stages WHERE id = ?", (stage_id,))
        if row is None:
            return None
        return _row(row)

    def seed_defaults(self, project_id: str) -> list[StageRecord]:
        """Insert the standard pipeline when a project has no stages yet."""
        existing = self.list_for_project(project_id)
        if existing:
            return existing
        now = utc_now()
        for position, (name, status) in enumerate(DEFAULT_PIPELINE):
            progress = "CURRENT" if position == 0 else "PENDING"
            self._db.execute(
                """
                INSERT INTO project_stages (
                    id, project_id, name, status, progress, position, notes,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (str(uuid.uuid4()), project_id, name, status, progress, position, now, now),
            )
        return self.list_for_project(project_id)

    def insert(
        self,
        *,
        project_id: str,
        name: str,
        status: str,
        progress: str,
        notes: str | None,
    ) -> StageRecord:
        rows = self.list_for_project(project_id)
        position = 0 if not rows else rows[-1].position + 1
        now = utc_now()
        stage_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO project_stages (
                id, project_id, name, status, progress, position, notes,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (stage_id, project_id, name, status, progress, position, notes, now, now),
        )
        saved = self.get(stage_id)
        if saved is None:
            raise RuntimeError("inserted stage disappeared")
        return saved

    def update(
        self,
        stage_id: str,
        *,
        name: str | None = None,
        notes: str | None = None,
        set_notes: bool = False,
        progress: str | None = None,
        status: str | None = None,
    ) -> StageRecord | None:
        current = self.get(stage_id)
        if current is None:
            return None
        self._db.execute(
            """
            UPDATE project_stages
            SET name = ?, status = ?, progress = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name.strip() if name else current.name,
                status or current.status,
                progress or current.progress,
                notes if set_notes else current.notes,
                utc_now(),
                stage_id,
            ),
        )
        return self.get(stage_id)

    def set_progress(self, stage_id: str, progress: str) -> None:
        self._db.execute(
            """
            UPDATE project_stages SET progress = ?, updated_at = ? WHERE id = ?
            """,
            (progress, utc_now(), stage_id),
        )

    def delete(self, stage_id: str) -> bool:
        return self._db.execute("DELETE FROM project_stages WHERE id = ?", (stage_id,)) > 0

    def reorder(self, project_id: str, stage_ids: list[str]) -> list[StageRecord]:
        current = self.list_for_project(project_id)
        if [stage.id for stage in current] == stage_ids:
            return current
        known = {stage.id for stage in current}
        if set(stage_ids) != known or len(stage_ids) != len(current):
            raise ValueError("stage order must list every stage of the project once")
        now = utc_now()
        with self._db.immediate() as conn:
            for index, stage_id in enumerate(stage_ids):
                conn.execute(
                    "UPDATE project_stages SET position = ? WHERE id = ? AND project_id = ?",
                    (-1 - index, stage_id, project_id),
                )
            for index, stage_id in enumerate(stage_ids):
                conn.execute(
                    """
                    UPDATE project_stages
                    SET position = ?, updated_at = ?
                    WHERE id = ? AND project_id = ?
                    """,
                    (index, now, stage_id, project_id),
                )
        return self.list_for_project(project_id)


def _row(row: object) -> StageRecord:
    return StageRecord(
        id=str(row["id"]),  # type: ignore[index]
        project_id=str(row["project_id"]),  # type: ignore[index]
        name=str(row["name"]),  # type: ignore[index]
        status=str(row["status"]),  # type: ignore[index]
        progress=str(row["progress"]),  # type: ignore[index]
        position=int(row["position"]),  # type: ignore[index]
        notes=None if row["notes"] is None else str(row["notes"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
    )
