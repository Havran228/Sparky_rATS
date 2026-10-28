from __future__ import annotations

import re
import time

from fastapi.testclient import TestClient
from tests.conftest import make_settings

from app.db.repositories.projects import ProjectRepository


def test_upload_match_profile_and_duplicate(
    client: TestClient,
    auth_header: dict[str, str],
) -> None:
    prompts: list[str] = []

    async def fake_structured(
        role: object,
        prompt: str,
        schema: type[object],
        *,
        system: str | None = None,
        images: list[str] | None = None,
    ) -> object:
        del role, system
        name = schema.__name__
        if name == "RequirementKeywords":
            return schema(
                keywords=[
                    {
                        "term": "Kubernetes",
                        "category": "technology",
                        "group": "mandatory",
                        "mandatory": True,
                    },
                    {
                        "term": "Go",
                        "category": "technology",
                        "group": "mandatory",
                        "mandatory": True,
                    },
                    {"term": "Home office", "category": "other", "group": "offering"},
                ]
            )
        if name == "JudgementBatch":
            prompts.append(prompt)
            items = []
            for block in prompt.split("\nRequirement: ")[1:]:
                term = block.split(" (", 1)[0]
                ids = re.findall(r"CHUNK id=(\S+)", block)
                excerpt = block.split("Retrieved chunks:", 1)[-1]
                pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
                if ids and pattern.search(excerpt):
                    items.append(
                        {
                            "term": term,
                            "light": "GREEN",
                            "rationale": "Cited.",
                            "chunk_ids": ids[:1],
                        }
                    )
                else:
                    items.append(
                        {"term": term, "light": "RED", "rationale": "No chunk.", "chunk_ids": []}
                    )
            return schema(items=items)
        if name == "SkillProposal":
            return schema(skills=[{"skill": "Kubernetes", "years": 4}], languages=[])
        if name == "VisionTranscript":
            assert images
            return schema(text="# Scan\n\nKubernetes certificate.", language="en")
        raise AssertionError(name)

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * 1024
            vector[abs(hash(text)) % 1024] = 1.0
            vectors.append(vector)
        return vectors

    client.app.state.ollama.structured = fake_structured
    client.app.state.ollama.embed = fake_embed

    created = client.post(
        "/bundles",
        headers=auth_header,
        json={"name": "Baseline", "kind": "historical_baseline"},
    )
    assert created.status_code == 201
    bundle_id = created.json()["id"]

    body = b"# Experience\n\nOperated Kubernetes clusters and wrote internal tools.\n"
    uploaded = client.post(
        f"/bundles/{bundle_id}/documents",
        headers=auth_header,
        files={"file": ("cv.md", body, "text/markdown")},
        data={"doc_type": "CV"},
    )
    assert uploaded.status_code == 202
    stored = _wait(client, uploaded.json()["job_id"], auth_header)
    assert stored["status"] == "DONE"
    assert stored["result"]["duplicate"] is False
    assert stored["result"]["embedded"] is True
    assert stored["result"]["chunk_count"] >= 1
    document_id = stored["result"]["document_id"]

    again = client.post(
        f"/bundles/{bundle_id}/documents",
        headers=auth_header,
        files={"file": ("cv-copy.md", body, "text/markdown")},
        data={"doc_type": "CV"},
    )
    duplicate = _wait(client, again.json()["job_id"], auth_header)
    assert duplicate["result"]["duplicate"] is True
    assert duplicate["result"]["document_id"] == document_id

    detail = client.get(f"/bundles/{bundle_id}", headers=auth_header)
    assert detail.status_code == 200
    assert len(detail.json()["documents"]) == 1

    scan = client.post(
        f"/bundles/{bundle_id}/documents",
        headers=auth_header,
        files={"file": ("scan.png", b"\x89PNG\r\nfake", "image/png")},
        data={"doc_type": "SCREENSHOT"},
    )
    scanned = _wait(client, scan.json()["job_id"], auth_header)
    assert scanned["status"] == "DONE"
    assert scanned["result"]["used_vision"] is True

    project = ProjectRepository(client.app.state.db).insert(
        source_kind="pasted_text",
        source_ref=None,
        company="Northwind",
        title="Platform engineer",
        contract_type="TPP",
        salary=None,
        language="en",
        requirements={"mandatory": ["Kubernetes", "Go"], "preferred": []},
        offerings=["Home office"],
        contacts=None,
        dates=None,
        raw_content_markdown="Kubernetes and Go.",
        model_name="test",
        prompt_version="extract_job_v1",
    )
    saved_profile = client.put(
        "/profile",
        headers=auth_header,
        json={
            "identity": {"name": "Ada"},
            "skills": [{"skill": "Rust", "years": 2, "confirmed": True, "evidence_bundle_ids": []}],
        },
    )
    assert saved_profile.status_code == 200
    assert saved_profile.json()["identity"]["name"] == "Ada"

    started = client.post(
        f"/projects/{project.id}/evaluate",
        headers=auth_header,
        json={"bundle_ids": [bundle_id]},
    )
    assert started.status_code == 202
    evaluated = _wait(client, started.json()["job_id"], auth_header)
    assert evaluated["status"] == "DONE", evaluated.get("error")
    evaluation_id = evaluated["result"]["evaluation_id"]
    loaded = client.get(f"/evaluations/{evaluation_id}", headers=auth_header)
    assert loaded.status_code == 200
    payload = loaded.json()
    lights = {item["term"]: item for item in payload["classification"]["items"]}
    assert lights["Kubernetes"]["light"] == "GREEN"
    assert lights["Kubernetes"]["citations"][0]["excerpt"]
    assert lights["Go"]["light"] == "RED"
    assert payload["classification"]["profile_skills_used"] == ["Rust"]
    assert payload["score_weights"]["mandatory"] == 0.6
    assert payload["prompt_version"] == "classify_match_v1"
    assert "Rust" in prompts[0]
    hazards = [
        term["term"]
        for term in payload["lexical_report"]["terms"]
        if term["filter_hazard"]
    ]
    assert "Go" in hazards

    proposed = client.post(
        "/profile/propose",
        headers=auth_header,
        json={"bundle_ids": [bundle_id]},
    )
    proposal = _wait(client, proposed.json()["job_id"], auth_header)
    assert proposal["status"] == "DONE", proposal.get("error")
    profile = client.get("/profile", headers=auth_header).json()
    by_skill = {item["skill"]: item for item in profile["skills"]}
    assert by_skill["Rust"]["confirmed"] is True
    assert by_skill["Kubernetes"]["confirmed"] is False

    removed = client.delete(f"/bundles/{bundle_id}", headers=auth_header)
    assert removed.status_code == 200
    assert client.get(f"/bundles/{bundle_id}", headers=auth_header).status_code == 404


def test_inbox_drop_lands_in_the_inbox_bundle(tmp_path, monkeypatch) -> None:
    from app.main import create_app

    settings = make_settings(tmp_path, monkeypatch, inbox_debounce_seconds=0.05)
    application = create_app(settings)
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(application) as client:

        async def fake_embed(texts: list[str]) -> list[list[float]]:
            return [[0.0] * 1024 for _ in texts]

        client.app.state.ollama.embed = fake_embed
        (settings.inbox_dir / "notes.md").write_text(
            "# Notes\n\nKept Kubernetes runbooks for the team.\n",
            encoding="utf-8",
        )
        deadline = time.monotonic() + 4
        document_count = 0
        while time.monotonic() < deadline:
            listing = client.get("/bundles", headers=headers)
            assert listing.status_code == 200
            inbox = [item for item in listing.json()["bundles"] if item["name"] == "Inbox"]
            if inbox and inbox[0]["document_count"] >= 1:
                document_count = inbox[0]["document_count"]
                break
            time.sleep(0.05)
    assert document_count >= 1


def _wait(client: TestClient, job_id: str, auth_header: dict[str, str]) -> dict[str, object]:
    deadline = time.monotonic() + 4
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=auth_header)
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"DONE", "FAILED", "CANCELLED"}:
            return last
        time.sleep(0.02)
    raise AssertionError(last)
