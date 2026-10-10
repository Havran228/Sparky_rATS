"""Project rows written by ingestion (ADR-006)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    source_kind: str
    source_ref: str | None
    company: str | None
    title: str | None
    contract_type: str | None
    salary: dict[str, object] | None
    language: str | None
    requirements: dict[str, object] | None
    offerings: list[object] | None
    contacts: list[object] | None
    dates: dict[str, object] | None
    raw_content_markdown: str | None
    current_status: str
    model_name: str | None
    prompt_version: str | None
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "company": self.company,
            "title": self.title,
            "contract_type": self.contract_type,
            "salary": self.salary,
            "language": self.language,
            "requirements": self.requirements,
            "offerings": self.offerings,
            "contacts": self.contacts,
            "dates": self.dates,
            "raw_content_markdown": self.raw_content_markdown,
            "current_status": self.current_status,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProjectRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(
        self,
        *,
        source_kind: str,
        source_ref: str | None,
        company: str | None,
        title: str | None,
        contract_type: str | None,
        salary: object,
        language: str | None,
        requirements: object,
        offerings: object,
        contacts: object,
        dates: object,
        raw_content_markdown: str | None,
        model_name: str | None,
        prompt_version: str | None,
    ) -> ProjectRecord:
        now = utc_now()
        project_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO projects (
                id, source_kind, source_ref, company, title, contract_type, salary_json,
                language, requirements_json, offerings_json, contacts_json, dates_json,
                raw_content_markdown, current_status, model_name, prompt_version,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW', ?, ?, ?, ?
            )
            """,
            (
                project_id,
                source_kind,
                source_ref,
                company,
                title,
                contract_type,
                _dump(salary),
                language,
                _dump(requirements),
                _dump(offerings),
                _dump(contacts),
                _dump(dates),
                raw_content_markdown,
                model_name,
                prompt_version,
                now,
                now,
            ),
        )
        saved = self.get(project_id)
        if saved is None:
            raise RuntimeError("inserted project disappeared")
        return saved

    def list_projects(self) -> list[ProjectRecord]:
        rows = self._db.query("SELECT * FROM projects ORDER BY created_at DESC, id DESC")
        return [_row(row) for row in rows]

    def get(self, project_id: str) -> ProjectRecord | None:
        row = self._db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if row is None:
            return None
        return _row(row)


def _dump(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _load(raw: object) -> object | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    loaded: object = json.loads(raw)
    return loaded


def _row(row: object) -> ProjectRecord:
    salary = _load(row["salary_json"])  # type: ignore[index]
    requirements = _load(row["requirements_json"])  # type: ignore[index]
    offerings = _load(row["offerings_json"])  # type: ignore[index]
    contacts = _load(row["contacts_json"])  # type: ignore[index]
    dates = _load(row["dates_json"])  # type: ignore[index]
    return ProjectRecord(
        id=str(row["id"]),  # type: ignore[index]
        source_kind=str(row["source_kind"]),  # type: ignore[index]
        source_ref=None if row["source_ref"] is None else str(row["source_ref"]),  # type: ignore[index]
        company=None if row["company"] is None else str(row["company"]),  # type: ignore[index]
        title=None if row["title"] is None else str(row["title"]),  # type: ignore[index]
        contract_type=None if row["contract_type"] is None else str(row["contract_type"]),  # type: ignore[index]
        salary=salary if isinstance(salary, dict) else None,
        language=None if row["language"] is None else str(row["language"]),  # type: ignore[index]
        requirements=requirements if isinstance(requirements, dict) else None,
        offerings=offerings if isinstance(offerings, list) else None,
        contacts=contacts if isinstance(contacts, list) else None,
        dates=dates if isinstance(dates, dict) else None,
        raw_content_markdown=(
            None
            if row["raw_content_markdown"] is None  # type: ignore[index]
            else str(row["raw_content_markdown"])  # type: ignore[index]
        ),
        current_status=str(row["current_status"]),  # type: ignore[index]
        model_name=None if row["model_name"] is None else str(row["model_name"]),  # type: ignore[index]
        prompt_version=(
            None if row["prompt_version"] is None else str(row["prompt_version"])  # type: ignore[index]
        ),
        created_at=str(row["created_at"]),  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
    )
