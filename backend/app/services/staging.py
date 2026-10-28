"""In-memory clipboard for replies the owner sends themselves (ADR-011).

Drafts are not written to Gmail, Calendar, or the database. A restart drops them.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass

from app.clock import utc_now


@dataclass(frozen=True)
class Draft:
    id: str
    project_id: str
    subject: str
    body: str
    created_at: str

    def public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "subject": self.subject,
            "body": self.body,
            "created_at": self.created_at,
        }


class StagingBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._drafts: dict[str, Draft] = {}

    def put(self, *, project_id: str, subject: str, body: str) -> Draft:
        cleaned_subject = subject.strip()
        cleaned_body = body.strip()
        if not cleaned_subject:
            raise ValueError("subject is empty")
        if not cleaned_body:
            raise ValueError("body is empty")
        draft = Draft(
            id=str(uuid.uuid4()),
            project_id=project_id,
            subject=cleaned_subject,
            body=cleaned_body,
            created_at=utc_now(),
        )
        with self._lock:
            self._drafts[draft.id] = draft
        return draft

    def list_for_project(self, project_id: str) -> list[Draft]:
        with self._lock:
            found = [draft for draft in self._drafts.values() if draft.project_id == project_id]
        return sorted(found, key=lambda draft: (draft.created_at, draft.id))

    def delete(self, draft_id: str) -> bool:
        with self._lock:
            return self._drafts.pop(draft_id, None) is not None
