from __future__ import annotations

import pytest

from app.db.database import Database
from app.db.repositories.projects import ProjectRepository
from app.db.repositories.stages import StageRecord
from app.services.stages import derive_status


def _stage(status: str, progress: str, position: int) -> StageRecord:
    return StageRecord(
        id=f"s{position}",
        project_id="p",
        name=status,
        status=status,
        progress=progress,
        position=position,
        notes=None,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


def test_derive_status_from_the_open_stage() -> None:
    stages = [
        _stage("NEW", "DONE", 0),
        _stage("CV_DRAFT", "DONE", 1),
        _stage("SUBMITTED", "CURRENT", 2),
        _stage("SCREENING", "PENDING", 3),
    ]
    assert derive_status(stages) == "SUBMITTED"


def test_terminal_progress_outranks_the_current_stage() -> None:
    stages = [
        _stage("SCREENING", "WITHDRAWN", 2),
        _stage("INTERVIEW", "CURRENT", 3),
    ]
    assert derive_status(stages) == "WITHDRAWN"


def test_latest_terminal_wins_and_empty_is_new() -> None:
    assert derive_status([]) == "NEW"
    stages = [
        _stage("SCREENING", "GHOSTED", 2),
        _stage("OFFER", "ON_HOLD", 4),
    ]
    assert derive_status(stages) == "ON_HOLD"
    done = [_stage("NEW", "DONE", 0), _stage("OFFER", "DONE", 5)]
    assert derive_status(done) == "OFFER"


def test_move_recomputes_current_status_and_kanban_columns(client, auth_header) -> None:
    project = ProjectRepository(client.app.state.db).insert(
        source_kind="text",
        source_ref=None,
        company="Acme",
        title="Platform",
        contract_type="TPP",
        salary=None,
        language="en",
        requirements=None,
        offerings=None,
        contacts=None,
        dates=None,
        raw_content_markdown=None,
        model_name=None,
        prompt_version=None,
    )
    listed = client.get(f"/projects/{project.id}/stages", headers=auth_header)
    assert listed.status_code == 200
    body = listed.json()
    assert body["current_status"] == "NEW"
    assert [stage["status"] for stage in body["stages"]][:3] == ["NEW", "CV_DRAFT", "SUBMITTED"]

    moved = client.post(
        f"/projects/{project.id}/move",
        headers=auth_header,
        json={"column": "SCREENING"},
    )
    assert moved.status_code == 200
    screening = moved.json()
    assert screening["project"]["current_status"] == "SCREENING"
    progress = {stage["status"]: stage["progress"] for stage in screening["stages"]}
    assert progress["NEW"] == "DONE"
    assert progress["CV_DRAFT"] == "DONE"
    assert progress["SUBMITTED"] == "DONE"
    assert progress["SCREENING"] == "CURRENT"
    assert progress["OFFER"] == "PENDING"

    held = client.post(
        f"/projects/{project.id}/move",
        headers=auth_header,
        json={"column": "ON_HOLD"},
    )
    assert held.json()["project"]["current_status"] == "ON_HOLD"

    direct = client.patch(
        f"/projects/{project.id}",
        headers=auth_header,
        json={"current_status": "OFFER"},
    )
    assert direct.status_code == 405

    board = client.get("/board", headers=auth_header)
    assert board.status_code == 200
    columns = {column["status"]: column["projects"] for column in board.json()["columns"]}
    assert any(item["id"] == project.id for item in columns["ON_HOLD"])
    assert columns["OFFER"] == []


def test_stage_crud_reorder_and_refuse_last_delete(client, auth_header) -> None:
    project = _project(client)
    created = client.post(
        f"/projects/{project.id}/stages",
        headers=auth_header,
        json={"name": "Round 2", "status": "ROUND_2", "notes": "panel"},
    )
    assert created.status_code == 201
    stages = created.json()["stages"]
    extra = stages[-1]
    assert extra["status"] == "ROUND_2"
    assert extra["progress"] == "PENDING"

    patched = client.patch(
        f"/projects/{project.id}/stages/{extra['id']}",
        headers=auth_header,
        json={"notes": "take-home", "progress": "CURRENT"},
    )
    assert patched.status_code == 200
    project_row = ProjectRepository(client.app.state.db).get(project.id)
    assert project_row is not None
    assert project_row.current_status == "ROUND_2"

    ids = [stage["id"] for stage in patched.json()["stages"]]
    rotated = ids[-1:] + ids[:-1]
    ordered = client.post(
        f"/projects/{project.id}/stages/order",
        headers=auth_header,
        json={"stage_ids": rotated},
    )
    assert ordered.status_code == 200
    assert [stage["id"] for stage in ordered.json()["stages"]] == rotated

    removed = client.delete(
        f"/projects/{project.id}/stages/{extra['id']}",
        headers=auth_header,
    )
    assert removed.status_code == 200
    remaining = removed.json()["stages"]
    assert all(stage["id"] != extra["id"] for stage in remaining)
    last = remaining[0]
    for stage in remaining[1:]:
        assert (
            client.delete(
                f"/projects/{project.id}/stages/{stage['id']}",
                headers=auth_header,
            ).status_code
            == 200
        )
    refused = client.delete(
        f"/projects/{project.id}/stages/{last['id']}",
        headers=auth_header,
    )
    assert refused.status_code == 422


def _project(client):
    return ProjectRepository(client.app.state.db).insert(
        source_kind="text",
        source_ref=None,
        company=None,
        title="Role",
        contract_type=None,
        salary=None,
        language=None,
        requirements=None,
        offerings=None,
        contacts=None,
        dates=None,
        raw_content_markdown=None,
        model_name=None,
        prompt_version=None,
    )


def test_legacy_project_without_stages_is_seeded(settings) -> None:
    db = Database(settings.db_path)
    try:
        db.migrate()
        db.execute(
            """
            INSERT INTO projects (id, source_kind, current_status, created_at, updated_at)
            VALUES ('legacy', 'text', 'SUBMITTED', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """
        )
        from app.services.stages import ensure_stages

        stages = ensure_stages(db, "legacy")
        status = db.query_one("SELECT current_status FROM projects WHERE id = 'legacy'")
    finally:
        db.close()
    assert stages[0].progress == "CURRENT"
    assert status is not None
    assert str(status["current_status"]) == "NEW"


@pytest.mark.parametrize(
    ("column", "status"),
    [("withdrawn", "WITHDRAWN"), ("GHOSTED", "GHOSTED"), ("rejected", "REJECTED")],
)
def test_terminal_columns(client, auth_header, column: str, status: str) -> None:
    project = _project(client)
    moved = client.post(
        f"/projects/{project.id}/move",
        headers=auth_header,
        json={"column": column},
    )
    assert moved.status_code == 200
    assert moved.json()["project"]["current_status"] == status
