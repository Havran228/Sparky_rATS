from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm.ollama_client import (
    ModelRole,
    OllamaClient,
    OllamaError,
    StructuredOutputError,
)


class Label(BaseModel):
    label: str


@pytest.mark.asyncio
async def test_structured_retries_once_then_validates(settings) -> None:
    calls: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        calls.append(body)
        if len(calls) == 1:
            content = '{"nope": true}'
        else:
            content = '{"label": "ok"}'
        return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(settings, http=http)
        result = await client.structured(ModelRole.fast, "extract", Label, system="be precise")

    assert result.label == "ok"
    assert len(calls) == 2
    assert calls[0]["model"] == settings.model_fast
    assert calls[0]["keep_alive"] == settings.keep_alive_fast
    assert calls[0]["options"] == {"temperature": 0}
    assert calls[0]["stream"] is False
    format_schema = calls[0]["format"]
    assert isinstance(format_schema, dict) and format_schema["title"] == "Label"
    messages = calls[1]["messages"]
    assert isinstance(messages, list)
    assert any("failed validation" in str(item.get("content")) for item in messages)


@pytest.mark.asyncio
async def test_structured_does_not_retry_transport_errors(settings) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": "busy"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(settings, http=http)
        with pytest.raises(OllamaError):
            await client.structured(ModelRole.heavy, "extract", Label)
    assert calls == 1
    assert client.model_name(ModelRole.heavy) == settings.model_heavy


@pytest.mark.asyncio
async def test_validation_failure_is_reported_after_the_retry(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "nope"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(settings, http=http)
        with pytest.raises(StructuredOutputError):
            await client.structured(ModelRole.vision, "extract", Label)


@pytest.mark.asyncio
async def test_health_reads_loaded_models(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ps"
        return httpx.Response(200, json={"models": [{"name": "bge-m3:latest"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://ollama") as http:
        client = OllamaClient(settings, http=http)
        health = await client.health()
    assert health.ok is True
    assert health.loaded_models == ["bge-m3:latest"]
