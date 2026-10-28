"""Role-addressed Ollama client with a validating structured-output helper (ADR-018)."""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class ModelRole(StrEnum):
    fast = "fast"
    heavy = "heavy"
    vision = "vision"
    embed = "embed"


class OllamaError(RuntimeError):
    """Ollama could not be reached or returned an error."""


class StructuredOutputError(OllamaError):
    """The model response did not match the schema after one retry."""


class OllamaHealth(BaseModel):
    ok: bool
    loaded_models: list[str] = []
    detail: str | None = None


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, object] = {}


class ChatTurn(BaseModel):
    content: str
    tool_calls: list[ToolCall] = []


class OllamaClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = http is None
        self._http = http or httpx.AsyncClient(
            base_url=settings.ollama_base_url,
            timeout=settings.ollama_timeout_seconds,
        )
        self._models = {
            ModelRole.fast: settings.model_fast,
            ModelRole.heavy: settings.model_heavy,
            ModelRole.vision: settings.model_vision,
            ModelRole.embed: settings.model_embed,
        }
        self._keep_alive = {
            ModelRole.fast: settings.keep_alive_fast,
            ModelRole.heavy: settings.keep_alive_heavy,
            ModelRole.vision: settings.keep_alive_vision,
            ModelRole.embed: settings.keep_alive_embed,
        }

    def model_name(self, role: ModelRole) -> str:
        return self._models[role]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def health(self) -> OllamaHealth:
        try:
            response = await self._http.get(
                "/api/ps",
                timeout=self._settings.ollama_health_timeout_seconds,
            )
            response.raise_for_status()
        except Exception as exc:
            return OllamaHealth(ok=False, detail=type(exc).__name__)
        body = response.json()
        models = body.get("models", []) if isinstance(body, dict) else []
        names: list[str] = []
        if isinstance(models, list):
            for item in models:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    names.append(item["name"])
        return OllamaHealth(ok=True, loaded_models=names)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {
            "model": self.model_name(ModelRole.embed),
            "input": texts,
            "keep_alive": self._keep_alive[ModelRole.embed],
        }
        try:
            response = await self._http.post("/api/embed", json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise OllamaError(f"{type(exc).__name__}: {exc}") from exc
        body = response.json()
        raw = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(raw, list) or len(raw) != len(texts):
            raise OllamaError("Ollama embed response did not include one vector per input")
        vectors: list[list[float]] = []
        for item in raw:
            if not isinstance(item, list) or not item:
                raise OllamaError("Ollama embed vector was empty")
            vectors.append([float(value) for value in item])
        return vectors

    async def structured(
        self,
        role: ModelRole,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
        images: list[str] | None = None,
    ) -> T:
        messages: list[dict[str, object]] = []
        if system:
            messages.append({"role": "system", "content": system})
        user: dict[str, object] = {"role": "user", "content": prompt}
        if images:
            user["images"] = images
        messages.append(user)
        schema_format = schema.model_json_schema()
        last_error = "response did not match the schema"
        for attempt in (1, 2):
            content = await self._chat(role, messages, schema_format)
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = str(exc)
                if attempt == 2:
                    break
                log.info("structured output for %s failed validation; retrying once", role.value)
                messages.append({"role": "assistant", "content": content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "The previous response failed validation: "
                            f"{last_error}. Reply with JSON matching the schema."
                        ),
                    }
                )
        raise StructuredOutputError(last_error)

    async def chat(
        self,
        role: ModelRole,
        messages: list[dict[str, object]],
        *,
        tools: list[dict[str, object]] | None = None,
    ) -> ChatTurn:
        """Free-form chat. Tool calls are returned; this method does not execute them."""
        payload: dict[str, object] = {
            "model": self.model_name(role),
            "messages": messages,
            "stream": False,
            "keep_alive": self._keep_alive[role],
            "options": {"temperature": 0},
        }
        if tools:
            payload["tools"] = tools
        try:
            response = await self._http.post("/api/chat", json=payload)
            response.raise_for_status()
        except Exception as exc:
            raise OllamaError(f"{type(exc).__name__}: {exc}") from exc
        body = response.json()
        message = body.get("message") if isinstance(body, dict) else None
        if not isinstance(message, dict):
            raise OllamaError("Ollama response did not include a message")
        content = message.get("content")
        return ChatTurn(
            content=content if isinstance(content, str) else "",
            tool_calls=_tool_calls(message.get("tool_calls")),
        )

    async def _chat(
        self,
        role: ModelRole,
        messages: list[dict[str, object]],
        schema_format: dict[str, object],
    ) -> str:
        payload = {
            "model": self.model_name(role),
            "messages": messages,
            "format": schema_format,
            "stream": False,
            "keep_alive": self._keep_alive[role],
            "options": {"temperature": 0},
        }
        try:
            response = await self._http.post("/api/chat", json=payload)
            response.raise_for_status()
        except StructuredOutputError:
            raise
        except Exception as exc:
            raise OllamaError(f"{type(exc).__name__}: {exc}") from exc
        body = response.json()
        message = body.get("message") if isinstance(body, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise OllamaError("Ollama response did not include message content")
        return content


def _tool_calls(raw: object) -> list[ToolCall]:
    if not isinstance(raw, list):
        return []
    calls: list[ToolCall] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        arguments = function.get("arguments")
        parsed: dict[str, object] = {}
        if isinstance(arguments, dict):
            parsed = {str(key): value for key, value in arguments.items()}
        elif isinstance(arguments, str) and arguments.strip():
            loaded = json.loads(arguments)
            if isinstance(loaded, dict):
                parsed = {str(key): value for key, value in loaded.items()}
        calls.append(ToolCall(name=name, arguments=parsed))
    return calls
