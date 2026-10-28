from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from tests.conftest import make_settings

from app.llm.schemas.documents import (
    CoverLetterModel,
    CoverRecipient,
    EducationEntry,
    ExperienceEntry,
    ResumeContact,
    ResumeLanguage,
    ResumeModel,
    SkillGroup,
)
from app.llm.schemas.profile import CandidateProfile
from app.services.ats_engine import Keyword, lexical_report_text, load_aliases
from app.services.doc_generator import (
    ground_cover_letter,
    ground_resume,
    letter_date,
    matches_confirmed,
    mentioned,
)
from app.services.json_diff import json_diff
from app.services.typst_compile import (
    TEMPLATES_DIR,
    CompileError,
    compile_raw_markup,
    compile_template,
)

_FORBIDDEN_TYPST = re.compile(r"#(table|image|rect|box|read|eval|include|import)\b")


def test_templates_are_single_column_text() -> None:
    english = (TEMPLATES_DIR / "resume_en.typ").read_text(encoding="utf-8")
    slovak = (TEMPLATES_DIR / "resume_sk.typ").read_text(encoding="utf-8")
    letter = (TEMPLATES_DIR / "cover_letter.typ").read_text(encoding="utf-8")
    for source in (english, slovak, letter):
        assert _FORBIDDEN_TYPST.search(source) is None
        assert "sys.inputs.data" in source
        assert "Liberation Sans" in source
    assert "Experience" in english
    assert "Zručnosti" in slovak
    assert "Prax" in slovak
    assert "Vzdelanie" in slovak


def test_grounding_keeps_confirmed_aliases_and_named_evidence() -> None:
    profile = CandidateProfile.model_validate(
        {
            "identity": {"name": "Ada Lovelace"},
            "contact": {"email": "ada@example.com"},
            "links": ["https://example.com/ada"],
            "languages": [{"language": "English", "cefr": "C1"}],
            "skills": [
                {"skill": "Kubernetes", "confirmed": True},
                {"skill": "COBOL", "confirmed": False},
            ],
        }
    )
    proposed = ResumeModel(
        contact=ResumeContact(name="Someone Else", email="wrong@example.com"),
        summary="Ran Kubernetes.",
        experience=[
            ExperienceEntry(company="Northwind", title="Engineer", bullets=["Kubernetes"]),
            ExperienceEntry(company="Initech", title="Analyst", bullets=["Invented"]),
        ],
        skills=[SkillGroup(name="Platform", items=["K8s", "COBOL", "JavaScript"])],
        education=[
            EducationEntry(institution="Faculty of Informatics", credential="Ing."),
            EducationEntry(institution="Imaginary University"),
        ],
        languages=[ResumeLanguage(language="Mandarin", level="C2")],
    )
    evidence = "Platform engineer at Northwind. Studied at Faculty of Informatics."
    grounded, report = ground_resume(proposed, profile, evidence, load_aliases())
    assert grounded.contact.name == "Ada Lovelace"
    assert grounded.contact.email == "ada@example.com"
    assert grounded.contact.links == ["https://example.com/ada"]
    assert [item.language for item in grounded.languages] == ["English"]
    assert grounded.skills[0].items == ["K8s"]
    assert [job.company for job in grounded.experience] == ["Northwind"]
    assert [item.institution for item in grounded.education] == ["Faculty of Informatics"]
    assert report.dropped_skills == ["COBOL", "JavaScript"]
    assert report.dropped_experience == ["Initech"]
    assert report.dropped_education == ["Imaginary University"]
    assert matches_confirmed("K8s", ["Kubernetes"], load_aliases())
    assert not matches_confirmed("Java", ["JavaScript"], load_aliases())
    assert mentioned("Go", "I use Go daily")
    assert not mentioned("Go", "Government platform")


def test_cover_letter_header_comes_from_the_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = CandidateProfile.model_validate({"identity": {"name": "Ada Lovelace"}})
    proposed = CoverLetterModel(
        contact=ResumeContact(name="Wrong"),
        recipient=CoverRecipient(company="Northwind"),
        salutation="Dobrý deň,",
        paragraphs=["Píšem Vám ohľadom pracovného pomeru."],
        closing="S pozdravom,",
        signature="Wrong",
    )
    settings = make_settings(tmp_path, monkeypatch)
    grounded = ground_cover_letter(proposed, profile, letter_date(settings))
    assert grounded.signature == "Ada Lovelace"
    assert grounded.contact.name == "Ada Lovelace"
    assert grounded.date == datetime.now(ZoneInfo("Europe/Bratislava")).date().isoformat()


def test_json_diff_walks_objects_and_lists() -> None:
    changes = json_diff(
        {"summary": "one", "skills": ["Kubernetes"]},
        {"summary": "two", "skills": ["Kubernetes", "Go"]},
    )
    by_path = {str(change["path"]): change["kind"] for change in changes}
    assert by_path["/summary"] == "changed"
    assert by_path["/skills/1"] == "added"


def test_pdf_text_match_is_word_boundary() -> None:
    report = lexical_report_text(
        [
            Keyword(term="Kubernetes", group="mandatory", category="technology"),
            Keyword(term="Go", group="mandatory", category="technology"),
            Keyword(term="Java", group="preferred", category="technology"),
        ],
        "Operated Kubernetes clusters. JavaScript only.",
        aliases=load_aliases(),
    )
    by_term = {term.term: term for term in report.terms}
    assert by_term["Kubernetes"].hit_count == 1
    assert by_term["Go"].filter_hazard is True
    assert by_term["Java"].hit_count == 0


def test_compile_invocation_stays_inside_a_secret_free_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, kwargs))
        cwd = Path(str(kwargs["cwd"]))
        if Path(command[0]).name == "pdftotext":
            return subprocess.CompletedProcess(command, 0, stdout=b"Ada Lovelace", stderr=b"")
        assert (cwd / "model.json").is_file()
        assert (cwd / "main.typ").is_file()
        (cwd / "out.pdf").write_bytes(b"%PDF-1.4\n%")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr("app.services.typst_compile.subprocess.run", fake_run)
    settings = make_settings(tmp_path, monkeypatch)
    monkeypatch.setenv("SPARKY_MASTER_KEY", "super-secret-key")
    model = ResumeModel(
        contact=ResumeContact(name="Ada Lovelace"),
        summary="Kubernetes",
        skills=[SkillGroup(name="Platform", items=["Kubernetes"])],
    )
    compiled = compile_template(
        "resume_en.typ",
        model_json=json.dumps(model.model_dump(mode="json")),
        language="en",
        settings=settings,
    )
    assert compiled.text == "Ada Lovelace"
    assert compiled.pdf.startswith(b"%PDF")
    typst_command, typst_kwargs = calls[0]
    root = Path(str(typst_kwargs["cwd"]))
    assert typst_kwargs.get("shell") in {None, False}
    env = typst_kwargs["env"]
    assert isinstance(env, dict)
    assert "SPARKY_MASTER_KEY" not in env
    assert "super-secret-key" not in env.values()
    assert "--root" in typst_command
    assert Path(typst_command[typst_command.index("--root") + 1]) == root
    assert "--input" in typst_command
    assert "data=model.json" in typst_command
    assert str(settings.data_dir) not in typst_command


def test_typst_sandbox_blocks_file_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path, monkeypatch)
    secret = settings.data_dir / "needle.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("NEEDLE-SECRET-VALUE", encoding="utf-8")
    with pytest.raises(CompileError, match="import"):
        compile_raw_markup('#import "@preview/evil:0.1.0": *\n', language="en", settings=settings)
    with pytest.raises(CompileError):
        compile_raw_markup(f'#read("{secret}")\n', language="en", settings=settings)
    with pytest.raises(CompileError):
        compile_raw_markup('#read("/etc/passwd")\n', language="en", settings=settings)

    model = ResumeModel(
        contact=ResumeContact(name='#read("/etc/passwd")'),
        summary="Operated Kubernetes at Northwind.",
        experience=[
            ExperienceEntry(
                company="Northwind",
                title="Engineer",
                bullets=["Ran Kubernetes clusters."],
            )
        ],
        skills=[SkillGroup(name="Platform", items=["Kubernetes"])],
    )
    compiled = compile_template(
        "resume_en.typ",
        model_json=json.dumps(model.model_dump(mode="json"), ensure_ascii=False),
        language="en",
        settings=settings,
    )
    assert "Kubernetes" in compiled.text
    assert "Northwind" in compiled.text
    assert "root:x:0:0" not in compiled.text
    assert "NEEDLE-SECRET-VALUE" not in compiled.text
    slovak = compile_template(
        "resume_sk.typ",
        model_json=json.dumps(model.model_dump(mode="json"), ensure_ascii=False),
        language="sk",
        settings=settings,
    )
    assert "Zručnosti" in slovak.text
    letter = compile_template(
        "cover_letter.typ",
        model_json=json.dumps(
            {
                "contact": {"name": "Ada Lovelace", "email": "ada@example.com", "links": []},
                "date": "2026-10-28",
                "recipient": {"company": "Northwind"},
                "role": "Platform engineer",
                "salutation": "Dobrý deň,",
                "paragraphs": ["Píšem Vám ohľadom pracovného pomeru a Kubernetes."],
                "closing": "S pozdravom,",
                "signature": "Ada Lovelace",
            },
            ensure_ascii=False,
        ),
        language="sk",
        settings=settings,
    )
    assert "Dobrý deň" in letter.text
    assert "Kubernetes" in letter.text


def test_generate_version_diff_submit_and_raw_path(
    client: TestClient,
    auth_header: dict[str, str],
) -> None:
    prompts: list[tuple[str, str]] = []
    resumes = {"n": 0}

    async def fake_structured(
        role: object,
        prompt: str,
        schema: type[object],
        *,
        system: str | None = None,
        images: list[str] | None = None,
    ) -> object:
        del images
        prompts.append((schema.__name__, prompt if system is None else f"{system}\n{prompt}"))
        assert str(role) == "heavy" or getattr(role, "value", None) == "heavy"
        if schema.__name__ == "ResumeModel":
            resumes["n"] += 1
            summary = "First summary with Kubernetes." if resumes["n"] == 1 else "Second summary."
            return schema(
                contact={"name": "Wrong Name"},
                summary=summary,
                experience=[
                    {
                        "company": "Northwind",
                        "title": "Platform engineer",
                        "bullets": ["Operated Kubernetes clusters."],
                    },
                    {"company": "Initech", "title": "Analyst", "bullets": ["Invented a role."]},
                ],
                skills=[{"name": "Platform", "items": ["Kubernetes", "K8s", "COBOL"]}],
                education=[
                    {"institution": "Faculty of Informatics", "credential": "Ing."},
                    {"institution": "Imaginary University"},
                ],
                languages=[{"language": "Mandarin", "level": "C2"}],
            )
        if schema.__name__ == "CoverLetterModel":
            return schema(
                contact={"name": "Wrong Name"},
                recipient={"company": "Northwind"},
                role="Platform engineer",
                salutation="Dobrý deň,",
                paragraphs=["Píšem Vám, lebo som robila s Kubernetes v Northwind."],
                closing="S pozdravom,",
                signature="Wrong Name",
            )
        raise AssertionError(schema.__name__)

    async def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1024 for _ in texts]

    client.app.state.ollama.structured = fake_structured
    client.app.state.ollama.embed = fake_embed

    created = client.post(
        "/bundles",
        headers=auth_header,
        json={"name": "Baseline", "kind": "historical_baseline"},
    )
    bundle_id = created.json()["id"]
    uploaded = client.post(
        f"/bundles/{bundle_id}/documents",
        headers=auth_header,
        files={
            "file": (
                "history.md",
                b"# Work\n\nPlatform engineer at Northwind. Operated Kubernetes.\n\n"
                b"# School\n\nStudied at Faculty of Informatics.\n",
                "text/markdown",
            )
        },
        data={"doc_type": "CV"},
    )
    stored = _wait(client, uploaded.json()["job_id"], auth_header)
    assert stored["status"] == "DONE", stored.get("error")

    profile = client.put(
        "/profile",
        headers=auth_header,
        json={
            "identity": {"name": "Ada Lovelace"},
            "contact": {"email": "ada@example.com", "location": "Bratislava"},
            "links": ["https://example.com/ada"],
            "languages": [{"language": "English", "cefr": "C1"}],
            "skills": [
                {"skill": "Kubernetes", "years": 4, "confirmed": True, "evidence_bundle_ids": []},
                {"skill": "COBOL", "years": 1, "confirmed": False, "evidence_bundle_ids": []},
            ],
        },
    )
    assert profile.status_code == 200
    project_id = _project(client)

    first = _generate(client, auth_header, project_id, bundle_id, language="en")
    assert first["version"] == 1
    document_id = str(first["document_id"])
    detail = client.get(f"/documents/{document_id}", headers=auth_header)
    assert detail.status_code == 200
    body = detail.json()
    assert body["content"]["contact"]["name"] == "Ada Lovelace"
    assert body["content"]["contact"]["email"] == "ada@example.com"
    assert body["content"]["languages"] == [{"language": "English", "level": "C1"}]
    assert body["content"]["skills"] == [{"name": "Platform", "items": ["Kubernetes", "K8s"]}]
    assert [item["company"] for item in body["content"]["experience"]] == ["Northwind"]
    assert [item["institution"] for item in body["content"]["education"]] == [
        "Faculty of Informatics"
    ]
    assert body["prompt_version"] == "tailor_resume_v1"
    assert body["template"] == "resume_en.typ"
    report = body["ats_roundtrip_report"]
    assert "Go" in report["mandatory_missing"]
    assert "Kubernetes" not in report["mandatory_missing"]
    assert report["grounding"]["dropped_skills"] == ["COBOL"]
    assert "COBOL" not in prompts[0][1]
    assert "Ada Lovelace" in prompts[0][1]
    assert "do not invent" in prompts[0][1].lower()
    assert "Northwind" in prompts[0][1]

    pdf = client.get(f"/documents/{document_id}/pdf", headers=auth_header)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    assert "inline" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF")

    second = _generate(client, auth_header, project_id, bundle_id, language="en")
    assert second["version"] == 2
    diff = client.get(
        f"/documents/{document_id}/diff/{second['document_id']}",
        headers=auth_header,
    )
    assert diff.status_code == 200
    paths = {change["path"]: change["kind"] for change in diff.json()["changes"]}
    assert paths["/summary"] == "changed"

    submitted = _wait(
        client,
        client.post(f"/documents/{document_id}/submit", headers=auth_header).json()["job_id"],
        auth_header,
    )
    assert submitted["status"] == "DONE", submitted.get("error")
    assert submitted["result"]["duplicate"] is False
    assert submitted["result"]["embedded"] is True
    again = _wait(
        client,
        client.post(f"/documents/{document_id}/submit", headers=auth_header).json()["job_id"],
        auth_header,
    )
    assert again["result"]["duplicate"] is True
    listing = client.get("/bundles", headers=auth_header).json()["bundles"]
    active = [item for item in listing if item["kind"] == "active_application"]
    assert len(active) == 1
    assert active[0]["project_id"] == project_id
    assert active[0]["document_count"] == 1
    marked = client.get(f"/documents/{document_id}", headers=auth_header).json()
    assert marked["is_submitted"] is True

    slovak = _generate(client, auth_header, project_id, bundle_id, language="sk")
    assert slovak["version"] == 1
    slovak_body = client.get(f"/documents/{slovak['document_id']}", headers=auth_header).json()
    assert slovak_body["template"] == "resume_sk.typ"

    letter_job = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_header,
        json={"doc_type": "COVER_LETTER", "language": "sk", "bundle_ids": [bundle_id]},
    )
    letter = _wait(client, letter_job.json()["job_id"], auth_header)
    assert letter["status"] == "DONE", letter.get("error")
    letter_body = client.get(
        f"/documents/{letter['result']['document_id']}",
        headers=auth_header,
    ).json()
    assert letter_body["content"]["signature"] == "Ada Lovelace"
    today = datetime.now(ZoneInfo("Europe/Bratislava")).date().isoformat()
    assert letter_body["content"]["date"] == today
    assert "vykanie" in prompts[-1][1]
    assert letter_body["prompt_version"] == "cover_letter_v1"

    raw_job = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_header,
        json={
            "doc_type": "CV",
            "language": "en",
            "bundle_ids": [bundle_id],
            "raw_markup": (
                '#set page(paper: "a4", margin: 1.8cm)\n'
                '#set text(font: ("Liberation Sans", "Roboto"), size: 12pt)\n'
                "Raw compile mentions Kubernetes.\n"
            ),
        },
    )
    raw = _wait(client, raw_job.json()["job_id"], auth_header)
    assert raw["status"] == "DONE", raw.get("error")
    raw_body = client.get(f"/documents/{raw['result']['document_id']}", headers=auth_header).json()
    assert raw_body["content"]["mode"] == "raw_markup"
    assert raw_body["template"] == "raw"
    assert raw_body["model_name"] is None

    blocked = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_header,
        json={
            "doc_type": "CV",
            "language": "en",
            "raw_markup": '#read("/etc/passwd")\n',
        },
    )
    failed = _wait(client, blocked.json()["job_id"], auth_header)
    assert failed["status"] == "FAILED"

    client.app.state.db.execute(
        "UPDATE generated_documents SET pdf_path = ? WHERE id = ?",
        ("/etc/passwd", document_id),
    )
    missing = client.get(f"/documents/{document_id}/pdf", headers=auth_header)
    assert missing.status_code == 404


def test_generation_requires_a_name_skills_and_evidence(
    client: TestClient,
    auth_header: dict[str, str],
) -> None:
    async def fake_structured(*args: object, **kwargs: object) -> object:
        raise AssertionError("the model should not be called")

    client.app.state.ollama.structured = fake_structured
    project_id = _project(client)
    missing_profile = _wait(
        client,
        client.post(
            f"/projects/{project_id}/documents",
            headers=auth_header,
            json={"doc_type": "CV", "language": "en", "bundle_ids": ["missing"]},
        ).json()["job_id"],
        auth_header,
    )
    assert missing_profile["status"] == "FAILED"
    assert "name" in str(missing_profile["error"])

    client.put(
        "/profile",
        headers=auth_header,
        json={"identity": {"name": "Ada Lovelace"}, "skills": []},
    )
    missing_skill = _wait(
        client,
        client.post(
            f"/projects/{project_id}/documents",
            headers=auth_header,
            json={"doc_type": "CV", "language": "en"},
        ).json()["job_id"],
        auth_header,
    )
    assert missing_skill["status"] == "FAILED"
    assert "skill" in str(missing_skill["error"])

    client.put(
        "/profile",
        headers=auth_header,
        json={
            "identity": {"name": "Ada Lovelace"},
            "skills": [{"skill": "Kubernetes", "confirmed": True, "evidence_bundle_ids": []}],
        },
    )
    missing_evidence = _wait(
        client,
        client.post(
            f"/projects/{project_id}/documents",
            headers=auth_header,
            json={"doc_type": "CV", "language": "en"},
        ).json()["job_id"],
        auth_header,
    )
    assert missing_evidence["status"] == "FAILED"
    assert "bundle" in str(missing_evidence["error"])
    unknown = client.post(
        "/projects/nope/documents",
        headers=auth_header,
        json={"doc_type": "CV", "language": "en"},
    )
    assert unknown.status_code == 404


def _project(client: TestClient) -> str:
    from app.db.repositories.projects import ProjectRepository

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
    return project.id


def _generate(
    client: TestClient,
    auth_header: dict[str, str],
    project_id: str,
    bundle_id: str,
    *,
    language: str,
) -> dict[str, object]:
    response = client.post(
        f"/projects/{project_id}/documents",
        headers=auth_header,
        json={"doc_type": "CV", "language": language, "bundle_ids": [bundle_id]},
    )
    assert response.status_code == 202
    finished = _wait(client, response.json()["job_id"], auth_header)
    assert finished["status"] == "DONE", finished.get("error")
    result = finished["result"]
    assert isinstance(result, dict)
    return result


def _wait(client: TestClient, job_id: str, auth_header: dict[str, str]) -> dict[str, object]:
    deadline = time.monotonic() + 20
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=auth_header)
        assert response.status_code == 200
        last = response.json()
        if last["status"] in {"DONE", "FAILED", "CANCELLED"}:
            return last
        time.sleep(0.02)
    raise AssertionError(last)
