from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.llm.ollama_client import ModelRole
from app.llm.schemas.job_posting import JobPostingExtract


def test_salary_endpoint_and_project_round_trip(
    client: TestClient,
    auth_header: dict[str, str],
) -> None:
    denied = client.post("/salary/net", json={"gross": "2500", "tax_year": 2026})
    assert denied.status_code == 401

    years = client.get("/salary/years", headers=auth_header)
    assert years.status_code == 200
    assert years.json()["years"] == [2025, 2026]

    net = client.post(
        "/salary/net",
        headers=auth_header,
        json={"gross": "2500", "tax_year": 2026, "claim_nczd": True, "contract_type": "TPP"},
    )
    assert net.status_code == 200
    body = net.json()
    assert body["modeled"] is True
    assert body["net"] == "1827.87"

    trade = client.post(
        "/salary/net",
        headers=auth_header,
        json={"gross": "3000", "tax_year": 2026, "contract_type": "SZCO"},
    )
    assert trade.status_code == 200
    assert trade.json()["modeled"] is False

    missing = client.post(
        "/salary/net",
        headers=auth_header,
        json={"gross": "3000", "tax_year": 1999},
    )
    assert missing.status_code == 404


def test_pasted_posting_becomes_a_project(
    client: TestClient,
    auth_header: dict[str, str],
) -> None:
    async def fake_structured(
        role: object,
        prompt: str,
        schema: type[JobPostingExtract],
        *,
        system: str | None = None,
    ) -> JobPostingExtract:
        assert role is ModelRole.fast
        assert "Northwind" in prompt
        return schema(
            title="Backend engineer",
            company="Northwind s.r.o.",
            contract_type="TPP",
            language="sk",
            salary={
                "minimum": "2500",
                "maximum": "3500",
                "period": "month",
                "basis": "gross",
                "raw": "2 500 - 3 500 € brutto",
            },
            mandatory_requirements=["Python"],
            preferred_requirements=["PostgreSQL"],
            offerings=["Home office"],
        )

    client.app.state.ollama.structured = fake_structured
    created = client.post(
        "/projects/ingest",
        headers=auth_header,
        json={"input": "Northwind s.r.o. hľadá Backend engineer. 2 500 - 3 500 € brutto."},
    )
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    job = _wait(client, job_id, auth_header)
    assert job["status"] == "DONE"
    project_id = job["result"]["project_id"]

    listing = client.get("/projects", headers=auth_header)
    assert listing.status_code == 200
    assert listing.json()["projects"][0]["id"] == project_id

    detail = client.get(f"/projects/{project_id}", headers=auth_header)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["title"] == "Backend engineer"
    assert payload["company"] == "Northwind s.r.o."
    assert payload["requirements"]["mandatory"] == ["Python"]
    assert payload["salary"]["minimum"] == "2500"
    assert payload["prompt_version"] == "extract_job_v1"


def test_empty_extract_is_not_saved(
    client: TestClient,
    auth_header: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_structured(
        role: object,
        prompt: str,
        schema: type[JobPostingExtract],
        *,
        system: str | None = None,
    ) -> JobPostingExtract:
        del role, prompt, system
        return schema(title="Operátor", company="Lightning Hub")

    client.app.state.ollama.structured = fake_structured
    with caplog.at_level("WARNING"):
        created = client.post(
            "/projects/ingest",
            headers=auth_header,
            json={"input": "Operátor call centra, Lightning Hub."},
        )
    assert created.status_code == 202
    job = _wait(client, created.json()["job_id"], auth_header)
    assert job["status"] == "FAILED"
    assert job["error"] is not None
    assert "not saved" in str(job["error"])
    listing = client.get("/projects", headers=auth_header)
    assert listing.json()["projects"] == []
    assert any("no requirements or offerings" in record.message for record in caplog.records)


def test_fetched_body_is_what_the_fast_model_sees(
    client: TestClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.scraper import FetchedPosting

    body = (
        "Náplň práce: telefonovať klientom a zapisovať požiadavky.\n\n"
        "Požiadavky: slovenský jazyk.\n\n"
        "Benefity: práca z domu."
    )
    prompts: list[str] = []

    async def fake_fetch(*args: object, **kwargs: object) -> FetchedPosting:
        del args, kwargs
        return FetchedPosting(
            url="https://www.profesia.sk/praca/lightning-hub/O5380068/",
            status_code=200,
            html="",
            markdown=body,
            job_posting={"@type": "JobPosting", "title": "Operátor"},
            used_fallback=False,
            cache_path="",
        )

    async def fake_structured(
        role: object,
        prompt: str,
        schema: type[JobPostingExtract],
        *,
        system: str | None = None,
    ) -> JobPostingExtract:
        del role, system
        prompts.append(prompt)
        return schema(
            title="Operátor",
            company="Lightning Hub",
            mandatory_requirements=["slovenský jazyk"],
            offerings=["práca z domu"],
        )

    monkeypatch.setattr("app.services.ingest.scraper.fetch_posting", fake_fetch)
    client.app.state.ollama.structured = fake_structured
    created = client.post(
        "/projects/ingest",
        headers=auth_header,
        json={"input": "https://www.profesia.sk/praca/lightning-hub/O5380068/"},
    )
    job = _wait(client, created.json()["job_id"], auth_header)
    assert job["status"] == "DONE", job.get("error")
    assert prompts and "telefonovať klientom" in prompts[0]
    assert "slovenský jazyk" in prompts[0]
    assert "práca z domu" in prompts[0]


def test_file_ingest_points_at_bundles(
    client: TestClient,
    auth_header: dict[str, str],
) -> None:
    created = client.post(
        "/projects/ingest",
        headers=auth_header,
        json={"input": "poster.jpg"},
    )
    assert created.status_code == 202
    job = _wait(client, created.json()["job_id"], auth_header)
    assert job["status"] == "FAILED"
    assert job["error"] is not None
    assert "knowledge bundle" in str(job["error"])


def _wait(client: TestClient, job_id: str, auth_header: dict[str, str]) -> dict[str, object]:
    deadline = time.monotonic() + 3
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=auth_header)
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"DONE", "FAILED", "CANCELLED"}:
            return last
        time.sleep(0.02)
    raise AssertionError(last)
