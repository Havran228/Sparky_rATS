"""Ollama tool-calling loop for one project thread (ADR-010, ADR-018).

Chat is the free-form exception. It writes project_chats only, and changes
other tables by calling tools.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.db.repositories.chats import ChatRecord, ChatRepository
from app.db.repositories.mcp_servers import McpServerRepository, McpToolRecord
from app.db.repositories.projects import ProjectRepository
from app.jobs import JobContext, JobRunner
from app.llm.ollama_client import ModelRole
from app.llm.prompts import load_prompt
from app.mcp_client import call_remote_tool, parse_alias, sanitize_tool_name, tool_alias
from app.runtime import Runtime
from app.tools.handlers import ToolError
from app.tools.registry import invoke, ollama_tools

PROMPT_NAME = "project_chat_v1"


def register_chat_handler(runner: JobRunner, runtime: Runtime) -> None:
    async def handle(ctx: JobContext) -> dict[str, object]:
        project_id = _required(ctx.payload, "project_id")
        thread_id = _required(ctx.payload, "thread_id")
        message = _required(ctx.payload, "message")
        ctx.progress(10, "reading the thread")
        result = await run_chat(
            project_id=project_id,
            thread_id=thread_id,
            message=message,
            runtime=runtime,
            progress=ctx.progress,
        )
        ctx.progress(100, "replied")
        return result

    runner.register("project_chat", handle, llm=True)


async def run_chat(
    *,
    project_id: str,
    thread_id: str,
    message: str,
    runtime: Runtime,
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, object]:
    project = ProjectRepository(runtime.db).get(project_id)
    if project is None:
        raise ValueError("project not found")
    chats = ChatRepository(runtime.db)
    chats.append(project_id=project_id, thread_id=thread_id, role="user", content=message)
    system, prompt_version = load_prompt(PROMPT_NAME)
    system = (
        f"{system}\n\n"
        f"Project id: {project.id}\n"
        f"Title: {project.title or 'unknown'}\n"
        f"Company: {project.company or 'unknown'}\n"
        f"Status: {project.current_status}"
    )
    history = chats.list_thread(project_id, thread_id)[-30:]
    messages: list[dict[str, object]] = [
        {"role": "system", "content": system},
        *_replay(history),
    ]
    tools = ollama_tools() + _external_schemas(runtime)
    cited: list[str] = []
    rounds = max(1, runtime.settings.chat_tool_rounds)
    for index in range(rounds):
        if progress is not None:
            progress(min(90, 30 + index * 15), "thinking")
        turn = await runtime.ollama.chat(ModelRole.fast, messages, tools=tools)
        if not turn.tool_calls:
            saved = chats.append(
                project_id=project_id,
                thread_id=thread_id,
                role="assistant",
                content=turn.content.strip() or "I have nothing to add.",
                referenced_chunks=_unique(cited),
            )
            return _done(saved, prompt_version, cited)
        messages.append(
            {
                "role": "assistant",
                "content": turn.content,
                "tool_calls": [
                    {"function": {"name": call.name, "arguments": call.arguments}}
                    for call in turn.tool_calls
                ],
            }
        )
        for call in turn.tool_calls:
            result, chunks = await _dispatch(call.name, call.arguments, runtime)
            cited.extend(chunks)
            encoded = json.dumps(result, ensure_ascii=False)
            chats.append(
                project_id=project_id,
                thread_id=thread_id,
                role="tool",
                content=encoded,
                referenced_chunks=chunks,
                tool_name=call.name,
                tool_arguments=call.arguments,
            )
            messages.append({"role": "tool", "tool_name": call.name, "content": encoded})
    saved = chats.append(
        project_id=project_id,
        thread_id=thread_id,
        role="assistant",
        content="I stopped after the tool limit. Ask me to continue from the results above.",
        referenced_chunks=_unique(cited),
    )
    return _done(saved, prompt_version, cited)


async def _dispatch(
    name: str,
    arguments: dict[str, object],
    runtime: Runtime,
) -> tuple[dict[str, object], list[str]]:
    parsed = parse_alias(name)
    if parsed is not None:
        server_id, safe_name = parsed
        tool = _whitelisted(runtime, server_id, safe_name)
        if tool is None:
            return {"error": f"{name} is not a whitelisted external tool"}, []
        server = McpServerRepository(runtime.db).get(server_id)
        if server is None:
            return {"error": "external MCP server is gone"}, []
        token = _remote_token(runtime, server_id)
        try:
            result = await call_remote_tool(server.url, token, tool.tool_name, arguments)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}, []
        return result, _chunk_ids(result)
    try:
        result = await invoke(name, arguments, runtime)
    except ToolError as exc:
        return {"error": str(exc)}, []
    return result, _chunk_ids(result)


def _whitelisted(runtime: Runtime, server_id: str, safe_name: str) -> McpToolRecord | None:
    for tool in McpServerRepository(runtime.db).list_tools(server_id):
        if tool.whitelisted and sanitize_tool_name(tool.tool_name) == safe_name:
            return tool
    return None


def _remote_token(runtime: Runtime, server_id: str) -> str | None:
    payload = runtime.secrets.get(f"mcp_remote:{server_id}")
    if payload is None:
        return None
    token = payload.get("token")
    if isinstance(token, str) and token:
        return token
    return None


def _external_schemas(runtime: Runtime) -> list[dict[str, object]]:
    tools: list[dict[str, object]] = []
    for server, tool in McpServerRepository(runtime.db).whitelisted_tools():
        schema = tool.input_schema or {"type": "object", "properties": {}}
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_alias(server.id, tool.tool_name),
                    "description": (
                        f"External tool from {server.name}. "
                        f"It can have side effects. {tool.description}"
                    ),
                    "parameters": schema,
                },
            }
        )
    return tools


def _replay(records: list[ChatRecord]) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    for record in records:
        if record.role == "tool" and record.tool_name:
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": record.tool_name,
                                "arguments": record.tool_arguments or {},
                            }
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_name": record.tool_name,
                    "content": record.content,
                }
            )
            continue
        messages.append({"role": record.role, "content": record.content})
    return messages


def _chunk_ids(payload: dict[str, object]) -> list[str]:
    raw = payload.get("chunks")
    if not isinstance(raw, list):
        return []
    found: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            chunk_id = item.get("chunk_id")
            if isinstance(chunk_id, str):
                found.append(chunk_id)
    return found


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _done(saved: ChatRecord, prompt_version: str, cited: list[str]) -> dict[str, object]:
    return {
        "message_id": saved.id,
        "thread_id": saved.thread_id,
        "content": saved.content,
        "referenced_chunks": _unique(cited),
        "prompt_version": prompt_version,
    }


def _required(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()
