"""Derive project status from stages, and move the pipeline (ADR-017).

The Kanban and the stage API write stage rows. ``projects.current_status`` is
rewritten afterwards and is not an input.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.clock import utc_now
from app.db.database import Database
from app.db.repositories.projects import ProjectRecord, ProjectRepository
from app.db.repositories.stages import (
    COLUMN_LABELS,
    PIPELINE_COLUMNS,
    PROGRESS_VALUES,
    TERMINAL_COLUMNS,
    StageRecord,
    StageRepository,
)

TERMINAL = frozenset(TERMINAL_COLUMNS)
PIPELINE = frozenset(PIPELINE_COLUMNS)
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")


class StageError(ValueError):
    """The requested stage change is not valid."""


def derive_status(stages: Sequence[StageRecord]) -> str:
    """Cached project status. A terminal progress outranks the open pipeline."""
    if not stages:
        return "NEW"
    terminals = [stage for stage in stages if stage.progress in TERMINAL]
    if terminals:
        return max(terminals, key=lambda stage: (stage.position, stage.id)).progress
    current = [stage for stage in stages if stage.progress == "CURRENT"]
    if current:
        chosen = min(current, key=lambda stage: (stage.position, stage.id))
        return chosen.status
    done = [stage for stage in stages if stage.progress == "DONE"]
    if done:
        chosen = max(done, key=lambda stage: (stage.position, stage.id))
        return chosen.status
    return "NEW"


def column_catalog(extra: Sequence[str] = ()) -> list[dict[str, str]]:
    ordered = list(PIPELINE_COLUMNS) + list(TERMINAL_COLUMNS)
    for code in extra:
        if code not in ordered:
            ordered.append(code)
    columns: list[dict[str, str]] = []
    for code in ordered:
        label = COLUMN_LABELS.get(code, code.replace("_", " ").title())
        columns.append({"status": code, "label": label})
    return columns


def ensure_stages(db: Database, project_id: str) -> list[StageRecord]:
    repository = StageRepository(db)
    existing = repository.list_for_project(project_id)
    if existing:
        return existing
    stages = repository.seed_defaults(project_id)
    _cache_status(db, project_id, stages)
    return stages


def move_project(
    db: Database,
    project_id: str,
    column: str,
) -> tuple[ProjectRecord, list[StageRecord]]:
    if ProjectRepository(db).get(project_id) is None:
        raise StageError("project not found")
    target = column.strip().upper()
    if not _CODE.match(target):
        raise StageError("column must be an uppercase status code")
    repository = StageRepository(db)
    stages = repository.seed_defaults(project_id)
    known = {stage.status for stage in stages}
    if target in TERMINAL:
        _apply_terminal(repository, stages, target)
    elif target in known or target in PIPELINE:
        _apply_column(repository, project_id, target)
    else:
        raise StageError(f"unknown column {target}")
    updated = repository.list_for_project(project_id)
    project = _cache_status(db, project_id, updated)
    if project is None:
        raise StageError("project not found")
    return project, updated


def add_stage(
    db: Database,
    project_id: str,
    *,
    name: str,
    status: str,
    notes: str | None,
) -> list[StageRecord]:
    if ProjectRepository(db).get(project_id) is None:
        raise StageError("project not found")
    cleaned = name.strip()
    code = status.strip().upper()
    if not cleaned:
        raise StageError("stage name is empty")
    if not _CODE.match(code):
        raise StageError("status must be an uppercase code such as ROUND_2")
    if code in TERMINAL:
        raise StageError("terminal outcomes are a progress value, not a new column stage")
    repository = StageRepository(db)
    repository.seed_defaults(project_id)
    repository.insert(
        project_id=project_id,
        name=cleaned,
        status=code,
        progress="PENDING",
        notes=notes,
    )
    stages = repository.list_for_project(project_id)
    _cache_status(db, project_id, stages)
    return stages


def patch_stage(
    db: Database,
    project_id: str,
    stage_id: str,
    *,
    name: str | None,
    notes: str | None,
    set_notes: bool,
    progress: str | None,
) -> list[StageRecord]:
    repository = StageRepository(db)
    current = repository.get(stage_id)
    if current is None or current.project_id != project_id:
        raise StageError("stage not found")
    if progress is not None and progress not in PROGRESS_VALUES:
        raise StageError("progress must be PENDING, CURRENT, DONE, or a terminal code")
    if progress == "CURRENT":
        for stage in repository.list_for_project(project_id):
            if stage.id != stage_id and stage.progress == "CURRENT":
                repository.set_progress(stage.id, "DONE")
    saved = repository.update(
        stage_id,
        name=name,
        notes=notes,
        set_notes=set_notes,
        progress=progress,
    )
    if saved is None:
        raise StageError("stage not found")
    stages = repository.list_for_project(project_id)
    _cache_status(db, project_id, stages)
    return stages


def delete_stage(db: Database, project_id: str, stage_id: str) -> list[StageRecord]:
    repository = StageRepository(db)
    stages = repository.list_for_project(project_id)
    target = next((stage for stage in stages if stage.id == stage_id), None)
    if target is None:
        raise StageError("stage not found")
    if len(stages) <= 1:
        raise StageError("a project keeps at least one stage")
    repository.delete(stage_id)
    remaining = repository.list_for_project(project_id)
    _cache_status(db, project_id, remaining)
    return remaining


def reorder_stages(db: Database, project_id: str, stage_ids: list[str]) -> list[StageRecord]:
    if ProjectRepository(db).get(project_id) is None:
        raise StageError("project not found")
    try:
        stages = StageRepository(db).reorder(project_id, stage_ids)
    except ValueError as exc:
        raise StageError(str(exc)) from exc
    _cache_status(db, project_id, stages)
    return stages


def _apply_terminal(repository: StageRepository, stages: list[StageRecord], target: str) -> None:
    chosen = _active_stage(stages)
    for stage in stages:
        if stage.id == chosen.id:
            repository.set_progress(stage.id, target)
        elif stage.progress in TERMINAL or stage.progress == "CURRENT":
            repository.set_progress(stage.id, "DONE")


def _apply_column(repository: StageRepository, project_id: str, target: str) -> None:
    stages = repository.list_for_project(project_id)
    match = next((stage for stage in stages if stage.status == target), None)
    if match is None:
        label = COLUMN_LABELS.get(target, target.replace("_", " ").title())
        match = repository.insert(
            project_id=project_id,
            name=label,
            status=target,
            progress="PENDING",
            notes=None,
        )
        stages = repository.list_for_project(project_id)
        order = {code: index for index, code in enumerate(PIPELINE_COLUMNS)}
        if target in order:
            others = [stage for stage in stages if stage.id != match.id]
            insert_at = sum(1 for stage in others if order.get(stage.status, 99) < order[target])
            ids = [stage.id for stage in others]
            ids.insert(insert_at, match.id)
            stages = repository.reorder(project_id, ids)
            match = next(stage for stage in stages if stage.id == match.id)
    for stage in stages:
        if stage.position < match.position:
            repository.set_progress(stage.id, "DONE")
        elif stage.id == match.id:
            repository.set_progress(stage.id, "CURRENT")
        else:
            repository.set_progress(stage.id, "PENDING")


def _active_stage(stages: list[StageRecord]) -> StageRecord:
    current = [stage for stage in stages if stage.progress == "CURRENT"]
    if current:
        return min(current, key=lambda stage: stage.position)
    done = [stage for stage in stages if stage.progress == "DONE"]
    if done:
        return max(done, key=lambda stage: stage.position)
    return stages[0]


def _cache_status(
    db: Database,
    project_id: str,
    stages: Sequence[StageRecord],
) -> ProjectRecord | None:
    status = derive_status(stages)
    db.execute(
        "UPDATE projects SET current_status = ?, updated_at = ? WHERE id = ?",
        (status, utc_now(), project_id),
    )
    return ProjectRepository(db).get(project_id)
