"""Role-addressed Ollama client with a validating structured-output helper (ADR-018)."""

from __future__ import annotations

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

    async def structured(
        self,
        role: ModelRole,
        prompt: str,
        schema: type[T],
        *,
        system: str | None = None,
    ) -> T:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
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

    async def _chat(
        self,
        role: ModelRole,
        messages: list[dict[str, str]],
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
