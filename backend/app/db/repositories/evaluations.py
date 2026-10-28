"""Persisted match evaluations (ADR-008)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database


@dataclass(frozen=True)
class EvaluationRecord:
    id: str
    project_id: str
    lexical_report: dict[str, object]
    classification: dict[str, object]
    match_percentage: float
    score_weights: dict[str, object]
    model_name: str
    prompt_version: str
    created_at: str

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "lexical_report": self.lexical_report,
            "classification": self.classification,
            "match_percentage": self.match_percentage,
            "score_weights": self.score_weights,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "created_at": self.created_at,
        }


class EvaluationRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(
        self,
        *,
        project_id: str,
        lexical_report: dict[str, object],
        classification: dict[str, object],
        match_percentage: float,
        score_weights: dict[str, float],
        model_name: str,
        prompt_version: str,
    ) -> EvaluationRecord:
        evaluation_id = str(uuid.uuid4())
        now = utc_now()
        self._db.execute(
            """
            INSERT INTO match_evaluations (
                id, project_id, lexical_report, classification, match_percentage,
                score_weights, model_name, prompt_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                project_id,
                json.dumps(lexical_report, ensure_ascii=False),
                json.dumps(classification, ensure_ascii=False),
                match_percentage,
                json.dumps(score_weights, ensure_ascii=False),
                model_name,
                prompt_version,
                now,
            ),
        )
        saved = self.get(evaluation_id)
        if saved is None:
            raise RuntimeError("inserted evaluation disappeared")
        return saved

    def get(self, evaluation_id: str) -> EvaluationRecord | None:
        row = self._db.query_one(
            "SELECT * FROM match_evaluations WHERE id = ?",
            (evaluation_id,),
        )
        if row is None:
            return None
        return _row(row)

    def list_for_project(self, project_id: str) -> list[EvaluationRecord]:
        rows = self._db.query(
            """
            SELECT * FROM match_evaluations
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (project_id,),
        )
        return [_row(row) for row in rows]


def _object(raw: object) -> dict[str, object]:
    if not isinstance(raw, str):
        return {}
    loaded = json.loads(raw)
    if isinstance(loaded, dict):
        return loaded
    return {}


def _row(row: object) -> EvaluationRecord:
    return EvaluationRecord(
        id=str(row["id"]),  # type: ignore[index]
        project_id=str(row["project_id"]),  # type: ignore[index]
        lexical_report=_object(row["lexical_report"]),  # type: ignore[index]
        classification=_object(row["classification"]),  # type: ignore[index]
        match_percentage=float(row["match_percentage"]),  # type: ignore[index]
        score_weights=_object(row["score_weights"]),  # type: ignore[index]
        model_name=str(row["model_name"]),  # type: ignore[index]
        prompt_version=str(row["prompt_version"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
    )
