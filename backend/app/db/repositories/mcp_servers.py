"""Registered external MCP servers and the per-tool whitelist (ADR-010)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database


@dataclass(frozen=True)
class McpServerRecord:
    id: str
    name: str
    url: str
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class McpToolRecord:
    server_id: str
    tool_name: str
    description: str
    input_schema: dict[str, object]
    whitelisted: bool

    def public_dict(self) -> dict[str, object]:
        return {
            "server_id": self.server_id,
            "tool_name": self.tool_name,
            "description": self.description,
            "input_schema": self.input_schema,
            "whitelisted": self.whitelisted,
            "side_effects": True,
        }


class McpServerRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, *, name: str, url: str) -> McpServerRecord:
        now = utc_now()
        server_id = str(uuid.uuid4())
        self._db.execute(
            """
            INSERT INTO external_mcp_servers (id, name, url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (server_id, name.strip(), url, now, now),
        )
        saved = self.get(server_id)
        if saved is None:
            raise RuntimeError("inserted MCP server disappeared")
        return saved

    def get(self, server_id: str) -> McpServerRecord | None:
        row = self._db.query_one(
            "SELECT * FROM external_mcp_servers WHERE id = ?",
            (server_id,),
        )
        if row is None:
            return None
        return _server(row)

    def list_servers(self) -> list[McpServerRecord]:
        rows = self._db.query(
            "SELECT * FROM external_mcp_servers ORDER BY created_at, id"
        )
        return [_server(row) for row in rows]

    def delete(self, server_id: str) -> bool:
        return (
            self._db.execute("DELETE FROM external_mcp_servers WHERE id = ?", (server_id,)) > 0
        )

    def replace_tools(
        self,
        server_id: str,
        tools: list[tuple[str, str, dict[str, object]]],
    ) -> list[McpToolRecord]:
        previous = {tool.tool_name: tool.whitelisted for tool in self.list_tools(server_id)}
        with self._db.immediate() as conn:
            conn.execute("DELETE FROM external_mcp_tools WHERE server_id = ?", (server_id,))
            for tool_name, description, schema in tools:
                conn.execute(
                    """
                    INSERT INTO external_mcp_tools (
                        server_id, tool_name, description, input_schema, whitelisted
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        server_id,
                        tool_name,
                        description,
                        json.dumps(schema),
                        1 if previous.get(tool_name) else 0,
                    ),
                )
        return self.list_tools(server_id)

    def list_tools(self, server_id: str | None = None) -> list[McpToolRecord]:
        if server_id is None:
            rows = self._db.query(
                "SELECT * FROM external_mcp_tools ORDER BY server_id, tool_name"
            )
        else:
            rows = self._db.query(
                """
                SELECT * FROM external_mcp_tools
                WHERE server_id = ?
                ORDER BY tool_name
                """,
                (server_id,),
            )
        return [_tool(row) for row in rows]

    def set_whitelisted(
        self,
        server_id: str,
        tool_name: str,
        whitelisted: bool,
    ) -> McpToolRecord | None:
        current = self._db.query_one(
            """
            SELECT * FROM external_mcp_tools
            WHERE server_id = ? AND tool_name = ?
            """,
            (server_id, tool_name),
        )
        if current is None:
            return None
        self._db.execute(
            """
            UPDATE external_mcp_tools
            SET whitelisted = ?
            WHERE server_id = ? AND tool_name = ?
            """,
            (1 if whitelisted else 0, server_id, tool_name),
        )
        updated = self._db.query_one(
            """
            SELECT * FROM external_mcp_tools
            WHERE server_id = ? AND tool_name = ?
            """,
            (server_id, tool_name),
        )
        if updated is None:
            return None
        return _tool(updated)

    def whitelisted_tools(self) -> list[tuple[McpServerRecord, McpToolRecord]]:
        rows = self._db.query(
            """
            SELECT s.id AS server_id, s.name, s.url, s.created_at, s.updated_at,
                   t.tool_name, t.description, t.input_schema, t.whitelisted
            FROM external_mcp_tools AS t
            JOIN external_mcp_servers AS s ON s.id = t.server_id
            WHERE t.whitelisted = 1
            ORDER BY s.name, t.tool_name
            """
        )
        found: list[tuple[McpServerRecord, McpToolRecord]] = []
        for row in rows:
            server = McpServerRecord(
                id=str(row["server_id"]),
                name=str(row["name"]),
                url=str(row["url"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            found.append((server, _tool(row)))
        return found


def _server(row: object) -> McpServerRecord:
    return McpServerRecord(
        id=str(row["id"]),  # type: ignore[index]
        name=str(row["name"]),  # type: ignore[index]
        url=str(row["url"]),  # type: ignore[index]
        created_at=str(row["created_at"]),  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
    )


def _tool(row: object) -> McpToolRecord:
    raw_schema = row["input_schema"]  # type: ignore[index]
    schema: dict[str, object] = {}
    if isinstance(raw_schema, str) and raw_schema:
        loaded = json.loads(raw_schema)
        if isinstance(loaded, dict):
            schema = {str(key): value for key, value in loaded.items()}
    return McpToolRecord(
        server_id=str(row["server_id"]),  # type: ignore[index]
        tool_name=str(row["tool_name"]),  # type: ignore[index]
        description=str(row["description"]),  # type: ignore[index]
        input_schema=schema,
        whitelisted=bool(int(row["whitelisted"])),  # type: ignore[index]
    )
