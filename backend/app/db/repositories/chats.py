"""Project chat transcripts (ADR-010)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from app.clock import utc_now
from app.db.database import Database


@dataclass(frozen=True)
class ChatRecord:
    id: str
    project_id: str
    thread_id: str
    role: str
    content: str
    referenced_chunks: list[str]
    tool_name: str | None
    tool_arguments: dict[str, object] | None
    created_at: str

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "thread_id": self.thread_id,
            "role": self.role,
            "content": self.content,
            "referenced_chunks": self.referenced_chunks,
            "tool_name": self.tool_name,
            "tool_arguments": self.tool_arguments,
            "created_at": self.created_at,
        }


class ChatRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def append(
        self,
        *,
        project_id: str,
        thread_id: str,
        role: str,
        content: str,
        referenced_chunks: list[str] | None = None,
        tool_name: str | None = None,
        tool_arguments: dict[str, object] | None = None,
    ) -> ChatRecord:
        now = utc_now()
        message_id = str(uuid.uuid4())
        chunks = referenced_chunks or []
        self._db.execute(
            """
            INSERT INTO project_chats (
                id, project_id, thread_id, role, content, referenced_chunks,
                tool_name, tool_arguments, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                project_id,
                thread_id,
                role,
                content,
                json.dumps(chunks),
                tool_name,
                None if tool_arguments is None else json.dumps(tool_arguments),
                now,
            ),
        )
        saved = self.get(message_id)
        if saved is None:
            raise RuntimeError("inserted chat row disappeared")
        return saved

    def get(self, message_id: str) -> ChatRecord | None:
        row = self._db.query_one("SELECT * FROM project_chats WHERE id = ?", (message_id,))
        if row is None:
            return None
        return _row(row)

    def list_thread(self, project_id: str, thread_id: str) -> list[ChatRecord]:
        rows = self._db.query(
            """
            SELECT * FROM project_chats
            WHERE project_id = ? AND thread_id = ?
            ORDER BY created_at, rowid
            """,
            (project_id, thread_id),
        )
        return [_row(row) for row in rows]

    def list_threads(self, project_id: str) -> list[dict[str, object]]:
        rows = self._db.query(
            """
            SELECT thread_id, MIN(created_at) AS started_at, COUNT(*) AS messages
            FROM project_chats
            WHERE project_id = ?
            GROUP BY thread_id
            ORDER BY started_at DESC
            """,
            (project_id,),
        )
        return [
            {
                "thread_id": str(row["thread_id"]),
                "started_at": str(row["started_at"]),
                "messages": int(row["messages"]),
            }
            for row in rows
        ]


def _row(row: object) -> ChatRecord:
    raw_chunks = row["referenced_chunks"]  # type: ignore[index]
    chunks: list[str] = []
    if isinstance(raw_chunks, str) and raw_chunks:
        loaded = json.loads(raw_chunks)
        if isinstance(loaded, list):
            chunks = [item for item in loaded if isinstance(item, str)]
    raw_args = row["tool_arguments"]  # type: ignore[index]
    arguments: dict[str, object] | None = None
    if isinstance(raw_args, str) and raw_args:
        loaded_args = json.loads(raw_args)
        if isinstance(loaded_args, dict):
            arguments = {str(key): value for key, value in loaded_args.items()}
    return ChatRecord(
        id=str(row["id"]),  # type: ignore[index]
        project_id=str(row["project_id"]),  # type: ignore[index]
        thread_id=str(row["thread_id"]),  # type: ignore[index]
        role=str(row["role"]),  # type: ignore[index]
        content=str(row["content"]),  # type: ignore[index]
        referenced_chunks=chunks,
        tool_name=None if row["tool_name"] is None else str(row["tool_name"]),  # type: ignore[index]
        tool_arguments=arguments,
        created_at=str(row["created_at"]),  # type: ignore[index]
    )
