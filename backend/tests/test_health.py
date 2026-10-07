from __future__ import annotations

from fastapi.testclient import TestClient

from app.llm.ollama_client import OllamaHealth


def test_health_is_degraded_when_ollama_is_down(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["db"]["ok"] is True
    assert body["qdrant"]["ok"] is True
    assert body["watcher"]["ok"] is True
    assert body["ollama"]["ok"] is False
    assert body["mcp"]["detail"] == "disabled"


def test_health_is_ok_when_ollama_answers(client: TestClient) -> None:
    class FakeOllama:
        async def health(self) -> OllamaHealth:
            return OllamaHealth(ok=True, loaded_models=["bge-m3:latest"])

        async def aclose(self) -> None:
            return None

    client.app.state.ollama = FakeOllama()
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["ollama"]["loaded_models"] == ["bge-m3:latest"]
