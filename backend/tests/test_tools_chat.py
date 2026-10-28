from __future__ import annotations

import json

import httpx

from app.db.repositories.bundles import ChunkDraft, KnowledgeRepository
from app.db.repositories.projects import ProjectRepository
from app.llm.ollama_client import OllamaClient
from app.tools.registry import TOOL_NAMES, invoke, tool_catalog


def test_registry_is_the_adr_tool_set() -> None:
    catalog = {item["name"]: item["side_effects"] for item in tool_catalog()}
    assert tuple(catalog) == TOOL_NAMES
    assert catalog["ingest_job"] is True
    assert catalog["evaluate_match"] is True
    assert catalog["compile_tailored_pdf"] is True
    assert catalog["list_projects"] is False
    assert catalog["query_knowledge_bundles"] is False
    assert catalog["calc_net_salary"] is False
    assert catalog["fetch_google_timeline"] is False
    assert catalog["get_project_details"] is False


def test_calc_net_salary_tool_uses_the_payroll_engine(client) -> None:
    import asyncio

    result = asyncio.run(
        invoke(
            "calc_net_salary",
            {"gross": "2000", "tax_year": 2026, "claim_nczd": True, "contract_type": "TPP"},
            client.app.state.runtime,
        )
    )
    assert result["modeled"] is True
    assert result["tax_year"] == 2026
    assert result["net"]


def test_query_tool_returns_chunk_ids(client) -> None:
    import asyncio

    bundle = KnowledgeRepository(client.app.state.db).insert_bundle(
        name="Baseline",
        kind="historical_baseline",
        project_id=None,
    )
    KnowledgeRepository(client.app.state.db).add_document(
        bundle_id=bundle.id,
        filename="cv.md",
        doc_type="CV",
        language="en",
        extracted_text="Built the billing service on Kubernetes.",
        content_hash="hash-1",
        file_path=None,
        mime_type="text/markdown",
        project_id=None,
        chunks=[
            ChunkDraft(
                chunk_index=0,
                section_header="Experience",
                content="Built the billing service on Kubernetes.",
                content_hash="chunk-hash",
                token_count=6,
                language="en",
                doc_type="CV",
            )
        ],
    )
    result = asyncio.run(
        invoke(
            "query_knowledge_bundles",
            {"query": "Kubernetes", "bundle_ids": [bundle.id], "limit": 5},
            client.app.state.runtime,
        )
    )
    assert result["chunks"]
    assert result["chunks"][0]["chunk_id"]
    assert "Kubernetes" in result["chunks"][0]["excerpt"]


def test_side_effect_tools_only_queue_jobs(client, auth_header) -> None:
    import asyncio

    queued = asyncio.run(
        invoke(
            "ingest_job",
            {"input": "1234567"},
            client.app.state.runtime,
        )
    )
    job = client.get(f"/jobs/{queued['job_id']}", headers=auth_header)
    assert job.status_code == 200
    assert job.json()["type"] == "ingest_job"


def test_project_chat_calls_a_tool_and_stores_citations(client, auth_header) -> None:
    runtime = client.app.state.runtime
    bundle = KnowledgeRepository(runtime.db).insert_bundle(
        name="Baseline",
        kind="historical_baseline",
        project_id=None,
    )
    document = KnowledgeRepository(runtime.db).add_document(
        bundle_id=bundle.id,
        filename="cv.md",
        doc_type="CV",
        language="en",
        extracted_text="Operated Kubernetes for the billing platform.",
        content_hash="hash-2",
        file_path=None,
        mime_type="text/markdown",
        project_id=None,
        chunks=[
            ChunkDraft(
                chunk_index=0,
                section_header="Experience",
                content="Operated Kubernetes for the billing platform.",
                content_hash="chunk-2",
                token_count=6,
                language="en",
                doc_type="CV",
            )
        ],
    )
    chunk_id = KnowledgeRepository(runtime.db).document_chunks(document.id)[0].id
    runtime.ollama = OllamaClient(runtime.settings, http=_scripted(chunk_id))
    project = ProjectRepository(runtime.db).insert(
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
    started = client.post(
        f"/projects/{project.id}/chat",
        headers=auth_header,
        json={"message": "Where is Kubernetes mentioned?"},
    )
    assert started.status_code == 202
    job_id = started.json()["job_id"]
    thread_id = started.json()["thread_id"]
    finished = _wait(client, auth_header, job_id)
    assert finished["status"] == "DONE", finished
    assert chunk_id in finished["result"]["referenced_chunks"]
    messages = client.get(
        f"/projects/{project.id}/chats",
        headers=auth_header,
        params={"thread_id": thread_id},
    )
    roles = [item["role"] for item in messages.json()["messages"]]
    assert roles == ["user", "tool", "assistant"]
    assert messages.json()["messages"][1]["tool_name"] == "query_knowledge_bundles"


def test_chat_refuses_an_external_tool_until_it_is_whitelisted(client, auth_header) -> None:
    runtime = client.app.state.runtime
    runtime.ollama = OllamaClient(runtime.settings, http=_external_then_text())
    project = ProjectRepository(runtime.db).insert(
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
    started = client.post(
        f"/projects/{project.id}/chat",
        headers=auth_header,
        json={"message": "Send the note."},
    )
    finished = _wait(client, auth_header, started.json()["job_id"])
    assert finished["status"] == "DONE", finished
    messages = client.get(
        f"/projects/{project.id}/chats",
        headers=auth_header,
        params={"thread_id": started.json()["thread_id"]},
    ).json()["messages"]
    tool_body = json.loads(messages[1]["content"])
    assert "not a whitelisted" in tool_body["error"]


def _scripted(chunk_id: str) -> httpx.AsyncClient:
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.calls = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.calls += 1
            if self.calls == 1:
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "query_knowledge_bundles",
                                "arguments": {"query": "Kubernetes", "limit": 3},
                            }
                        }
                    ],
                }
            else:
                message = {
                    "role": "assistant",
                    "content": f"Kubernetes is in chunk {chunk_id}.",
                }
            return httpx.Response(200, json={"message": message})

    return httpx.AsyncClient(transport=Transport(), base_url="http://ollama.test")


def _external_then_text() -> httpx.AsyncClient:
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.calls = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.calls += 1
            if self.calls == 1:
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "ext_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_send_mail",
                                "arguments": {},
                            }
                        }
                    ],
                }
            else:
                message = {"role": "assistant", "content": "I cannot call that."}
            return httpx.Response(200, json={"message": message})

    return httpx.AsyncClient(transport=Transport(), base_url="http://ollama.test")


def _wait(client, auth_header, job_id: str) -> dict[str, object]:
    import time

    last: dict[str, object] = {}
    for _ in range(50):
        response = client.get(f"/jobs/{job_id}", headers=auth_header)
        last = response.json()
        if last["status"] in {"DONE", "FAILED", "CANCELLED"}:
            return last
        time.sleep(0.05)
    return last
